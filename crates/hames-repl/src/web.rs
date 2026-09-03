use std::env;
use std::fs;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::{Arc, Mutex};

use anyhow::{Context, Result, bail};
use axum::Router;
use axum::body::{Body, to_bytes};
use axum::extract::{Path, Request, State};
use axum::http::header::{
    ACCEPT, CACHE_CONTROL, CONTENT_TYPE, COOKIE, HOST, LOCATION, ORIGIN, SET_COOKIE,
};
use axum::http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode, Uri};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get};
use futures_util::StreamExt;
use rust_embed::RustEmbed;
use serde::Serialize;
use uuid::Uuid;

use crate::api::{GatewayClient, PROTOCOL_VERSION};
use crate::local::{LocalPaths, ensure_gateway, ensure_search_setup};

const WEB_PROTOCOL_VERSION: u32 = 1;
const SESSION_COOKIE: &str = "hames_web_session";
const MAX_REQUEST_BYTES: usize = 8 * 1024 * 1024;
const CSRF_HEADER: &str = "x-hames-csrf";
const LAST_EVENT_ID: &str = "last-event-id";

#[derive(RustEmbed)]
#[folder = "assets/web"]
struct WebAssets;

#[derive(Clone)]
struct WebState {
    authority: Arc<str>,
    origin: Arc<str>,
    gateway_url: Arc<str>,
    gateway_token: Arc<str>,
    working_directory: Arc<str>,
    session_token: Arc<str>,
    csrf_token: Arc<str>,
    launch_token: Arc<Mutex<Option<String>>>,
    client: reqwest::Client,
}

#[derive(Serialize)]
struct Bootstrap<'a> {
    protocol_version: u32,
    gateway_protocol_version: u32,
    working_directory: &'a str,
    csrf_token: &'a str,
}

pub async fn run(port: u16, no_open: bool) -> Result<()> {
    let paths = LocalPaths::resolve()?;
    ensure_search_setup(&paths, false)?;
    ensure_gateway(&paths).await?;

    let gateway_url = paths.gateway_url()?;
    let health = GatewayClient::health_unauthenticated(&gateway_url).await?;
    if health.protocol_version != PROTOCOL_VERSION {
        bail!(
            "gateway protocol {} is incompatible with client protocol {}",
            health.protocol_version,
            PROTOCOL_VERSION
        );
    }

    let gateway_token = fs::read_to_string(&paths.token)
        .with_context(|| format!("failed to read {}", paths.token.display()))?;
    let listener =
        tokio::net::TcpListener::bind(SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port))
            .await
            .with_context(|| format!("failed to bind Hames web interface on 127.0.0.1:{port}"))?;
    let address = listener.local_addr()?;
    let authority = format!("127.0.0.1:{}", address.port());
    let launch_token = Uuid::new_v4().to_string();
    let state = WebState {
        authority: authority.clone().into(),
        origin: format!("http://{authority}").into(),
        gateway_url: gateway_url.into(),
        gateway_token: gateway_token.trim().to_owned().into(),
        working_directory: env::current_dir()?
            .canonicalize()?
            .to_string_lossy()
            .into_owned()
            .into(),
        session_token: Uuid::new_v4().to_string().into(),
        csrf_token: Uuid::new_v4().to_string().into(),
        launch_token: Arc::new(Mutex::new(Some(launch_token.clone()))),
        client: reqwest::Client::new(),
    };
    let launch_url = format!("http://{authority}/_hames/v1/launch/{launch_token}");

    println!("Hames web is available at http://{authority}");
    if !no_open && webbrowser::open(&launch_url).is_err() {
        eprintln!("Warning: could not open a browser; visit {launch_url}");
    } else if no_open {
        println!("Open {launch_url}");
    }

    axum::serve(listener, router(state))
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("Hames web server stopped unexpectedly")?;
    Ok(())
}

fn router(state: WebState) -> Router {
    Router::new()
        .route("/_hames/v1/launch/{token}", get(launch))
        .route("/_hames/v1/bootstrap", get(bootstrap))
        .route("/v1", any(proxy_root))
        .route("/v1/{*path}", any(proxy_path))
        .fallback(static_asset)
        .layer(middleware::from_fn_with_state(
            state.clone(),
            secure_loopback,
        ))
        .with_state(state)
}

async fn shutdown_signal() {
    if tokio::signal::ctrl_c().await.is_err() {
        std::future::pending::<()>().await;
    }
}

async fn secure_loopback(State(state): State<WebState>, request: Request, next: Next) -> Response {
    let host_matches = request
        .headers()
        .get(HOST)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|host| host == state.authority.as_ref());
    if !host_matches {
        return error_response(StatusCode::MISDIRECTED_REQUEST, "invalid_host");
    }

    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    headers.insert(
        "content-security-policy",
        HeaderValue::from_static(
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        ),
    );
    headers.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    headers.insert("x-frame-options", HeaderValue::from_static("DENY"));
    headers.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    headers.insert(
        "permissions-policy",
        HeaderValue::from_static("camera=(), microphone=(), geolocation=()"),
    );
    response
}

async fn launch(State(state): State<WebState>, Path(token): Path<String>) -> Response {
    let accepted = state.launch_token.lock().is_ok_and(|mut expected| {
        if expected.as_deref() == Some(token.as_str()) {
            expected.take();
            true
        } else {
            false
        }
    });
    if !accepted {
        return error_response(StatusCode::UNAUTHORIZED, "invalid_launch_token");
    }

    let cookie = format!(
        "{SESSION_COOKIE}={}; HttpOnly; SameSite=Strict; Path=/",
        state.session_token
    );
    (
        StatusCode::SEE_OTHER,
        [(SET_COOKIE, cookie), (LOCATION, "/chat".to_owned())],
    )
        .into_response()
}

async fn bootstrap(State(state): State<WebState>, headers: HeaderMap) -> Response {
    if !session_is_valid(&state, &headers) {
        return error_response(StatusCode::UNAUTHORIZED, "web_session_required");
    }
    axum::Json(Bootstrap {
        protocol_version: WEB_PROTOCOL_VERSION,
        gateway_protocol_version: PROTOCOL_VERSION,
        working_directory: &state.working_directory,
        csrf_token: &state.csrf_token,
    })
    .into_response()
}

async fn proxy_root(State(state): State<WebState>, request: Request) -> Response {
    proxy(state, request, "").await
}

async fn proxy_path(
    State(state): State<WebState>,
    Path(path): Path<String>,
    request: Request,
) -> Response {
    proxy(state, request, &path).await
}

async fn proxy(state: WebState, request: Request, path: &str) -> Response {
    if !session_is_valid(&state, request.headers()) {
        return error_response(StatusCode::UNAUTHORIZED, "web_session_required");
    }
    if request.method() != Method::GET && request.method() != Method::HEAD {
        let origin_valid = request
            .headers()
            .get(ORIGIN)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|origin| origin == state.origin.as_ref());
        let csrf_valid = request
            .headers()
            .get(CSRF_HEADER)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|token| token == state.csrf_token.as_ref());
        if !origin_valid || !csrf_valid {
            return error_response(StatusCode::FORBIDDEN, "csrf_check_failed");
        }
    }

    let (parts, body) = request.into_parts();
    let suffix = parts
        .uri
        .query()
        .map(|query| format!("?{query}"))
        .unwrap_or_default();
    let url = if path.is_empty() {
        format!("{}/v1{suffix}", state.gateway_url)
    } else {
        format!("{}/v1/{path}{suffix}", state.gateway_url)
    };
    let body = match to_bytes(body, MAX_REQUEST_BYTES).await {
        Ok(body) => body,
        Err(_) => return error_response(StatusCode::PAYLOAD_TOO_LARGE, "request_too_large"),
    };
    let mut upstream = state
        .client
        .request(parts.method, url)
        .bearer_auth(state.gateway_token.as_ref())
        .body(body);
    for name in [ACCEPT, CONTENT_TYPE, HeaderName::from_static(LAST_EVENT_ID)] {
        if let Some(value) = parts.headers.get(&name) {
            upstream = upstream.header(name, value);
        }
    }
    let upstream = match upstream.send().await {
        Ok(response) => response,
        Err(_) => return error_response(StatusCode::BAD_GATEWAY, "gateway_unavailable"),
    };
    let status = upstream.status();
    let response_headers = upstream.headers().clone();
    let stream = upstream
        .bytes_stream()
        .map(|result| result.map_err(std::io::Error::other));
    let mut response = Response::new(Body::from_stream(stream));
    *response.status_mut() = status;
    for name in [CONTENT_TYPE, CACHE_CONTROL] {
        if let Some(value) = response_headers.get(&name) {
            response.headers_mut().insert(name, value.clone());
        }
    }
    response
}

async fn static_asset(uri: Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let requested = if path.is_empty() { "index.html" } else { path };
    if let Some(asset) = WebAssets::get(requested) {
        return asset_response(requested, asset.data.into_owned());
    }
    if requested
        .rsplit('/')
        .next()
        .is_some_and(|name| name.contains('.'))
    {
        return error_response(StatusCode::NOT_FOUND, "asset_not_found");
    }
    match WebAssets::get("index.html") {
        Some(asset) => asset_response("index.html", asset.data.into_owned()),
        None => error_response(StatusCode::INTERNAL_SERVER_ERROR, "web_assets_missing"),
    }
}

fn asset_response(path: &str, bytes: Vec<u8>) -> Response {
    let mut response = bytes.into_response();
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_str(mime_guess::from_path(path).first_or_octet_stream().as_ref())
            .unwrap_or_else(|_| HeaderValue::from_static("application/octet-stream")),
    );
    response.headers_mut().insert(
        CACHE_CONTROL,
        if path == "index.html" {
            HeaderValue::from_static("no-store")
        } else {
            HeaderValue::from_static("public, max-age=31536000, immutable")
        },
    );
    response
}

fn session_is_valid(state: &WebState, headers: &HeaderMap) -> bool {
    headers
        .get_all(COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(';'))
        .filter_map(|cookie| cookie.trim().split_once('='))
        .any(|(name, value)| name == SESSION_COOKIE && value == state.session_token.as_ref())
}

fn error_response(status: StatusCode, code: &'static str) -> Response {
    (
        status,
        [(CONTENT_TYPE, "application/json")],
        format!(r#"{{"error":{{"code":"{code}","message":"{code}"}}}}"#),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;
    use axum::http::Request as HttpRequest;
    use axum::http::header::AUTHORIZATION;
    use axum::routing::post;
    use tower::ServiceExt;

    fn state() -> WebState {
        WebState {
            authority: "127.0.0.1:7412".into(),
            origin: "http://127.0.0.1:7412".into(),
            gateway_url: "http://127.0.0.1:9".into(),
            gateway_token: "gateway-secret".into(),
            working_directory: "/work/hames".into(),
            session_token: "browser-secret".into(),
            csrf_token: "csrf-secret".into(),
            launch_token: Arc::new(Mutex::new(Some("launch-secret".to_owned()))),
            client: reqwest::Client::new(),
        }
    }

    fn request(path: &str) -> HttpRequest<Body> {
        HttpRequest::builder()
            .uri(path)
            .header(HOST, "127.0.0.1:7412")
            .body(Body::empty())
            .unwrap()
    }

    #[tokio::test]
    async fn launch_token_is_single_use_and_sets_private_cookie() {
        let app = router(state());
        let rejected = app
            .clone()
            .oneshot(request("/_hames/v1/launch/wrong-token"))
            .await
            .unwrap();
        assert_eq!(rejected.status(), StatusCode::UNAUTHORIZED);

        let response = app
            .clone()
            .oneshot(request("/_hames/v1/launch/launch-secret"))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::SEE_OTHER);
        let cookie = response
            .headers()
            .get(SET_COOKIE)
            .unwrap()
            .to_str()
            .unwrap();
        assert!(cookie.contains("HttpOnly"));
        assert!(cookie.contains("SameSite=Strict"));

        let response = app
            .oneshot(request("/_hames/v1/launch/launch-secret"))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn bootstrap_requires_cookie_and_rejects_foreign_host() {
        let app = router(state());
        let response = app
            .clone()
            .oneshot(request("/_hames/v1/bootstrap"))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);

        let mut authorized = request("/_hames/v1/bootstrap");
        authorized.headers_mut().insert(
            COOKIE,
            HeaderValue::from_static("hames_web_session=browser-secret"),
        );
        let response = app.clone().oneshot(authorized).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 4096).await.unwrap();
        assert!(String::from_utf8_lossy(&body).contains("/work/hames"));

        let foreign = HttpRequest::builder()
            .uri("/")
            .header(HOST, "evil.invalid")
            .body(Body::empty())
            .unwrap();
        assert_eq!(
            app.oneshot(foreign).await.unwrap().status(),
            StatusCode::MISDIRECTED_REQUEST
        );
    }

    #[tokio::test]
    async fn static_shell_has_security_and_cache_headers() {
        let response = router(state()).oneshot(request("/chat")).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[CACHE_CONTROL], "no-store");
        assert!(response.headers().contains_key("content-security-policy"));
        assert!(response.headers().contains_key("x-content-type-options"));
    }

    #[tokio::test]
    async fn proxy_injects_bearer_and_preserves_event_resume() {
        async fn events(headers: HeaderMap) -> Response {
            assert_eq!(headers[AUTHORIZATION], "Bearer gateway-secret");
            assert_eq!(headers[LAST_EVENT_ID], "event-17");
            (
                [(CONTENT_TYPE, "text/event-stream")],
                "id: event-18\ndata: {\"type\":\"ready\"}\n\n",
            )
                .into_response()
        }

        let upstream = Router::new().route("/v1/events", get(events));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
        let mut proxy_state = state();
        proxy_state.gateway_url = format!("http://{address}").into();

        let mut proxy_request = request("/v1/events");
        proxy_request.headers_mut().insert(
            COOKIE,
            HeaderValue::from_static("hames_web_session=browser-secret"),
        );
        proxy_request.headers_mut().insert(
            HeaderName::from_static(LAST_EVENT_ID),
            HeaderValue::from_static("event-17"),
        );
        let response = router(proxy_state).oneshot(proxy_request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[CONTENT_TYPE], "text/event-stream");
        let body = to_bytes(response.into_body(), 4096).await.unwrap();
        assert!(String::from_utf8_lossy(&body).contains("event-18"));
        task.abort();
    }

    #[tokio::test]
    async fn mutations_require_matching_origin_and_csrf_token() {
        async fn mutate(headers: HeaderMap) -> Response {
            assert_eq!(headers[AUTHORIZATION], "Bearer gateway-secret");
            StatusCode::NO_CONTENT.into_response()
        }

        let upstream = Router::new().route("/v1/sessions", post(mutate));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
        let mut proxy_state = state();
        proxy_state.gateway_url = format!("http://{address}").into();
        let app = router(proxy_state);

        let mutation = || {
            HttpRequest::builder()
                .method(Method::POST)
                .uri("/v1/sessions")
                .header(HOST, "127.0.0.1:7412")
                .header(COOKIE, "hames_web_session=browser-secret")
                .body(Body::empty())
                .unwrap()
        };
        assert_eq!(
            app.clone().oneshot(mutation()).await.unwrap().status(),
            StatusCode::FORBIDDEN
        );

        let mut authorized = mutation();
        authorized
            .headers_mut()
            .insert(ORIGIN, HeaderValue::from_static("http://127.0.0.1:7412"));
        authorized.headers_mut().insert(
            HeaderName::from_static(CSRF_HEADER),
            HeaderValue::from_static("csrf-secret"),
        );
        assert_eq!(
            app.oneshot(authorized).await.unwrap().status(),
            StatusCode::NO_CONTENT
        );
        task.abort();
    }
}
