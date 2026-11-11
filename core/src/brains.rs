use anyhow::{bail, Result};
use futures::StreamExt;
use serde_json::Value;
use std::collections::HashMap;

pub const RANKS: [&str; 7] = ["imp", "fiend", "baron", "viscount", "marquis", "duke", "lord"];

#[derive(Debug, Clone, PartialEq)]
pub struct Brain {
    pub base: String,
    pub key: String,
    pub model: String,
}

pub fn resolve(settings: &HashMap<String, String>, requested: &str) -> Result<Brain> {
    let mut name = requested.trim().to_string();
    if RANKS.contains(&name.as_str()) {
        name = settings
            .get(&format!("rank.{name}"))
            .cloned()
            .unwrap_or_default()
            .trim()
            .to_string();
    }
    if name.is_empty() {
        name = String::new();
    }
    if let Some((provider, model_id)) = name.split_once('|') {
        let base = settings
            .get(&format!("provider.{provider}.base_url"))
            .cloned()
            .unwrap_or_default();
        let key = settings
            .get(&format!("provider.{provider}.api_key"))
            .cloned()
            .unwrap_or_default();
        let model_id = model_id.trim().to_string();
        if base.is_empty() || key.is_empty() || model_id.is_empty() {
            bail!("unknown provider: {provider}");
        }
        return Ok(Brain { base: base.trim_end_matches('/').to_string(), key, model: model_id });
    }
    let base = settings.get("llm_base_url").cloned().unwrap_or_default();
    let key = settings.get("llm_api_key").cloned().unwrap_or_default();
    let model = if name.is_empty() {
        settings.get("llm_model").cloned().unwrap_or_default()
    } else {
        name
    };
    if base.is_empty() || key.is_empty() || model.is_empty() {
        bail!("the provider settings are missing");
    }
    Ok(Brain { base: base.trim_end_matches('/').to_string(), key, model: model.trim().to_string() })
}

type OnDelta<'a> = &'a mut (dyn FnMut(&str) + Send);

pub async fn chat(
    client: &reqwest::Client,
    brain: &Brain,
    messages: &[Value],
) -> Result<Value> {
    chat_stream_full(client, brain, messages, &mut |_| {}, &mut |_| {}).await
}

pub async fn chat_stream(
    client: &reqwest::Client,
    brain: &Brain,
    messages: &[Value],
    on_delta: OnDelta<'_>,
) -> Result<Value> {
    chat_stream_full(client, brain, messages, on_delta, &mut |_| {}).await
}

pub async fn chat_stream_full(
    client: &reqwest::Client,
    brain: &Brain,
    messages: &[Value],
    on_delta: OnDelta<'_>,
    on_reasoning: OnDelta<'_>,
) -> Result<Value> {
    let body = serde_json::json!({
        "model": brain.model,
        "messages": messages,
        "temperature": 0.6,
        "tools": crate::tools::schema(),
        "reasoning_effort": "low",
        "stream": true,
    });
    let url = format!("{}/chat/completions", brain.base);
    let response = client
        .post(url)
        .bearer_auth(&brain.key)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await?;
    let status = response.status();
    if !status.is_success() {
        let text = response.text().await.unwrap_or_default();
        if status.as_u16() == 429 || status.is_server_error() {
            anyhow::bail!("provider down: {}: {}", status, snippet(&text));
        }
        anyhow::bail!("provider refused: {}: {}", status, snippet(&text));
    }
    let mut stream = response.bytes_stream();
    let mut buffer = String::new();
    let mut content = String::new();
    let mut calls: Vec<(usize, String, String, String)> = Vec::new();
    while let Some(chunk) = stream.next().await {
        let bytes = chunk?;
        buffer.push_str(&String::from_utf8_lossy(&bytes));
        while let Some(pos) = buffer.find('\n') {
            let line: String = buffer.drain(..=pos).collect();
            let line = line.trim();
            let payload = line.strip_prefix("data:").map(str::trim).unwrap_or("");
            if payload.is_empty() || payload == "[DONE]" {
                continue;
            }
            let value: Value = match serde_json::from_str(payload) {
                Ok(value) => value,
                Err(_) => continue,
            };
            let delta = value.pointer("/choices/0/delta").cloned().unwrap_or(Value::Null);
            if let Some(text) = delta
                .get("reasoning_content")
                .or_else(|| delta.get("reasoning"))
                .and_then(|t| t.as_str())
            {
                if !text.is_empty() {
                    on_reasoning(text);
                }
            }
            if let Some(text) = delta.get("content").and_then(|t| t.as_str()) {
                if !text.is_empty() {
                    content.push_str(text);
                    on_delta(text);
                }
            }
            for call in delta
                .get("tool_calls")
                .and_then(|calls| calls.as_array())
                .into_iter()
                .flatten()
            {
                let index = call.get("index").and_then(|i| i.as_u64()).unwrap_or(0) as usize;
                let entry = match calls.iter_mut().find(|(slot, _, _, _)| *slot == index) {
                    Some(entry) => entry,
                    None => {
                        calls.push((index, String::new(), String::new(), String::new()));
                        calls.last_mut().expect("just pushed")
                    }
                };
                if let Some(id) = call.get("id").and_then(|i| i.as_str()) {
                    entry.1.push_str(id);
                }
                if let Some(name) = call.pointer("/function/name").and_then(|n| n.as_str()) {
                    entry.2.push_str(name);
                }
                if let Some(args) = call.pointer("/function/arguments").and_then(|a| a.as_str()) {
                    entry.3.push_str(args);
                }
            }
        }
    }
    if content.is_empty() && calls.is_empty() {
        anyhow::bail!("the stream ended with nothing in it");
    }
    let mut message = serde_json::json!({"role": "assistant"});
    if !content.is_empty() {
        message["content"] = Value::String(content);
    }
    if !calls.is_empty() {
        calls.sort_by_key(|(slot, _, _, _)| *slot);
        message["tool_calls"] = Value::Array(
            calls
                .into_iter()
                .map(|(_, id, name, arguments)| {
                    serde_json::json!({
                        "id": id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    })
                })
                .collect(),
        );
    }
    Ok(message)
}

pub fn content_of(message: &Value) -> String {
    match message.get("content") {
        Some(Value::String(text)) => text.trim().to_string(),
        Some(Value::Array(blocks)) => blocks
            .iter()
            .filter_map(|block| {
                if block.get("type")?.as_str()? == "text" {
                    block.get("text").and_then(|t| t.as_str()).map(|t| t.to_string())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join(" "),
        _ => String::new(),
    }
}

fn snippet(text: &str) -> String {
    text.chars().take(200).collect()
}
