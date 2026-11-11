use crate::brains;
use crate::config;
use crate::sessions::{self, Store};
use crate::turn::{self, Runner};
use anyhow::Result;
use axum::body::Body;
use axum::extract::{Path as UrlPath, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use tokio::sync::Mutex;

type ApiError = (StatusCode, String);

fn internal(error: anyhow::Error) -> ApiError {
    (StatusCode::INTERNAL_SERVER_ERROR, error.to_string())
}

fn bad_gateway(error: anyhow::Error) -> ApiError {
    (StatusCode::BAD_GATEWAY, error.to_string())
}

struct AppState {
    store: Store,
    sessions: Mutex<HashMap<String, Arc<Mutex<Vec<Value>>>>>,
    cancels: Mutex<HashMap<String, Arc<AtomicBool>>>,
    interjects: Mutex<HashMap<String, Arc<std::sync::Mutex<Vec<String>>>>>,
    cwd: PathBuf,
}

impl AppState {
    async fn cancel_flag(&self, id: &str) -> Arc<AtomicBool> {
        self.cancels
            .lock()
            .await
            .entry(id.to_string())
            .or_insert_with(|| Arc::new(AtomicBool::new(false)))
            .clone()
    }

    async fn session_arc(&self, id: &str, messages: Vec<Value>) -> Arc<Mutex<Vec<Value>>> {
        self.sessions
            .lock()
            .await
            .entry(id.to_string())
            .or_insert_with(|| Arc::new(Mutex::new(messages)))
            .clone()
    }

    async fn interject_queue(&self, id: &str) -> Arc<std::sync::Mutex<Vec<String>>> {
        self.interjects
            .lock()
            .await
            .entry(id.to_string())
            .or_insert_with(|| Arc::new(std::sync::Mutex::new(Vec::new())))
            .clone()
    }
}

#[derive(Deserialize)]
struct TurnBody {
    text: String,
    #[serde(default)]
    brain: String,
}

#[derive(Deserialize)]
struct CreateBody {
    #[serde(default)]
    cwd: Option<String>,
}

pub async fn serve(port: u16, cwd: PathBuf) -> Result<()> {
    let state = Arc::new(AppState {
        store: Store::new(),
        sessions: Mutex::new(HashMap::new()),
        cancels: Mutex::new(HashMap::new()),
        interjects: Mutex::new(HashMap::new()),
        cwd,
    });
    let app = Router::new()
        .route("/sessions", get(list_sessions))
        .route("/sessions", post(create_session))
        .route("/sessions/{id}/turn", post(turn))
        .route("/sessions/{id}/turn/stream", post(turn_stream))
        .route("/sessions/{id}/cancel", post(cancel_turn))
        .route("/sessions/{id}/interject", post(interject))
        .route("/sessions/{id}", axum::routing::get(one_session))
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    println!("diablo core listening on 127.0.0.1:{port}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn list_sessions(State(state): State<Arc<AppState>>) -> Json<Value> {
    let items: Vec<Value> = state
        .store
        .list()
        .iter()
        .map(|record| {
            json!({
                "id": record.id,
                "title": record.title,
                "cwd": record.cwd,
                "updated": record.updated,
                "ago": sessions::timeago(&record.updated),
            })
        })
        .collect();
    Json(json!({"sessions": items}))
}

async fn one_session(
    State(state): State<Arc<AppState>>,
    UrlPath(id): UrlPath<String>,
) -> Json<Value> {
    match state.store.load(&id) {
        Some(record) => Json(serde_json::to_value(&record).unwrap_or(json!({}))),
        None => Json(json!({"error": "no such session"})),
    }
}

async fn create_session(
    State(state): State<Arc<AppState>>,
    body: Option<Json<CreateBody>>,
) -> Result<Json<Value>, ApiError> {
    let cwd = body
        .and_then(|Json(body)| body.cwd)
        .map(PathBuf::from)
        .unwrap_or_else(|| state.cwd.clone());
    let config = config::load().map_err(internal)?;
    let settings = config::settings(&config).await.map_err(bad_gateway)?;
    let brain_wanted = settings.get("llm_model").cloned().unwrap_or_default();
    brains::resolve(&settings, &brain_wanted).map_err(bad_gateway)?;
    let record = sessions::new_record(&cwd, &turn::system_prompt(&cwd));
    state.store.save(&record).map_err(internal)?;
    state.session_arc(&record.id, record.messages.clone()).await;
    Ok(Json(json!({"id": record.id, "title": record.title, "cwd": record.cwd})))
}

struct Prepared {
    record: sessions::Record,
    session_arc: Arc<Mutex<Vec<Value>>>,
    settings: HashMap<String, String>,
    brain_wanted: String,
    cancel: Arc<AtomicBool>,
}

async fn prepare(state: &AppState, id: &str) -> std::result::Result<Prepared, ApiError> {
    let record = state
        .store
        .load(id)
        .ok_or((StatusCode::NOT_FOUND, "no such session".to_string()))?;
    let session_arc = state.session_arc(id, record.messages.clone()).await;
    let config = config::load().map_err(internal)?;
    let settings = config::settings(&config).await.map_err(bad_gateway)?;
    let cancel = state.cancel_flag(id).await;
    Ok(Prepared {
        record,
        session_arc,
        brain_wanted: settings.get("llm_model").cloned().unwrap_or_default(),
        settings,
        cancel,
    })
}

fn finish(record: &mut sessions::Record, session: &[Value], store: &Store) -> Result<(), ApiError> {
    let mut messages = session.to_vec();
    Runner::trim(&mut messages);
    record.messages = messages;
    sessions::settle_title(record);
    record.updated = chrono::Local::now().to_rfc3339();
    store.save(record).map_err(internal)?;
    Ok(())
}

async fn turn(
    State(state): State<Arc<AppState>>,
    UrlPath(id): UrlPath<String>,
    Json(body): Json<TurnBody>,
) -> Result<Json<Value>, ApiError> {
    let prep = prepare(&state, &id).await?;
    let interject = state.interject_queue(&id).await;
    let runner = Runner::with_cancel(std::path::Path::new(&prep.record.cwd), prep.cancel.clone());
    let mut session = prep.session_arc.lock().await;
    let mut sink = |event: turn::StepEvent| {
        println!("{} {} {}", event.kind, event.name, event.detail);
        if !event.output.is_empty() {
            println!("  = {}", event.output.replace('\n', " "));
        }
    };
    let reply = runner
        .run_turn(
            &prep.settings,
            &prep.brain_wanted,
            &mut session,
            &body.text,
            &interject,
            &mut sink,
        )
        .await
        .map_err(bad_gateway)?;
    let mut record = prep.record.clone();
    for queued in interject.lock().unwrap().drain(..).collect::<Vec<String>>() {
        session.push(json!({"role": "user", "content": queued}));
    }
    finish(&mut record, &session, &state.store)?;
    Ok(Json(json!({"reply": reply, "id": record.id, "title": record.title})))
}

async fn turn_stream(
    State(state): State<Arc<AppState>>,
    UrlPath(id): UrlPath<String>,
    Json(body): Json<TurnBody>,
) -> Result<axum::response::Response, ApiError> {
    let prep = prepare(&state, &id).await?;
    let interject = state.interject_queue(&id).await;
    let runner = Runner::with_cancel(std::path::Path::new(&prep.record.cwd), prep.cancel.clone());
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<bytes::Bytes, std::io::Error>>(64);
    let store = Store::new();
    tokio::spawn(async move {
        let mut session = prep.session_arc.lock().await;
        let mut emit = |line: String| {
            let _ = tx.try_send(Ok(bytes::Bytes::from(format!("{line}\n"))));
        };
        let mut streamed = false;
        let result = runner
            .run_turn(
                &prep.settings,
                &prep.brain_wanted,
                &mut session,
                &body.text,
                &interject,
                &mut |event: turn::StepEvent| {
                    let payload = if event.kind == "reply_delta" {
                        streamed = true;
                        json!({"type": "reply_delta", "text": event.detail})
                    } else if event.kind == "thinking" {
                        json!({"type": "thinking", "text": event.detail})
                    } else if event.kind == "result" {
                        json!({"type": "result", "detail": event.detail, "output": event.output})
                    } else {
                        json!({"type": "tool", "detail": event.detail})
                    };
                    emit(payload.to_string());
                },
            )
            .await;
        match result {
            Ok(reply) => {
                if !streamed {
                    emit(json!({"type": "reply", "text": reply}).to_string());
                }
            }
            Err(error) => emit(json!({"type": "error", "message": error.to_string()}).to_string()),
        }
        let mut record = prep.record.clone();
        for queued in interject.lock().unwrap().drain(..).collect::<Vec<String>>() {
            session.push(json!({"role": "user", "content": queued}));
        }
        let _ = finish(&mut record, &session, &store);
        emit(json!({"type": "done"}).to_string());
    });
    let stream = tokio_stream::wrappers::ReceiverStream::new(rx);
    Ok((
        [(axum::http::header::CONTENT_TYPE, "application/x-ndjson")],
        Body::from_stream(stream),
    )
        .into_response())
}

#[derive(Deserialize)]
struct InterjectBody {
    text: String,
}

async fn interject(
    State(state): State<Arc<AppState>>,
    UrlPath(id): UrlPath<String>,
    Json(body): Json<InterjectBody>,
) -> Result<Json<Value>, ApiError> {
    if state.store.load(&id).is_none() {
        return Err((StatusCode::NOT_FOUND, "no such session".to_string()));
    }
    let queue = state.interject_queue(&id).await;
    queue.lock().unwrap().push(body.text);
    Ok(Json(json!({"queued": true})))
}

async fn cancel_turn(
    State(state): State<Arc<AppState>>,
    UrlPath(id): UrlPath<String>,
) -> Json<Value> {
    let flag = state.cancel_flag(&id).await;
    flag.store(true, std::sync::atomic::Ordering::Relaxed);
    Json(json!({"canceled": true}))
}
