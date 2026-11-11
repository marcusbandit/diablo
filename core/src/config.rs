use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Deserialize, Clone)]
pub struct LocalConfig {
    pub hub_url: String,
    pub hub_token: String,
}

pub fn config_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".config/diablo/config.toml")
}

pub fn load() -> Result<LocalConfig> {
    let path = config_path();
    let raw = std::fs::read_to_string(&path)
        .with_context(|| format!("reading {}", path.display()))?;
    let config: LocalConfig = toml::from_str(&raw).context("parsing config.toml")?;
    Ok(config)
}

pub async fn settings(config: &LocalConfig) -> Result<HashMap<String, String>> {
    let url = format!("{}/v1/settings", config.hub_url.trim_end_matches('/'));
    let response = reqwest::Client::new()
        .get(url)
        .header("X-Diablo-Token", &config.hub_token)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await?;
    let status = response.status();
    let body_text = response.text().await.unwrap_or_default();
    if !status.is_success() {
        anyhow::bail!(
            "hub returned {}: {}",
            status,
            body_text.chars().take(200).collect::<String>()
        );
    }
    let body: serde_json::Value = serde_json::from_str(&body_text).context("bad settings json")?;
    let mut map = HashMap::new();
    if let Some(entries) = body.get("settings").and_then(|v| v.as_object()) {
        for (key, value) in entries {
            let text = match value {
                serde_json::Value::String(text) => text.clone(),
                serde_json::Value::Null => String::new(),
                other => other.to_string(),
            };
            map.insert(key.clone(), text);
        }
    }
    Ok(map)
}
