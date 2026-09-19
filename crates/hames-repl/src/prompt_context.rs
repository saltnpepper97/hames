use serde_json::{Map, Value};

use crate::api::Event;

pub(crate) fn prompt_context_scope(event: &Event) -> String {
    let payload_agent = event
        .payload
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    format!(
        "{}|{}|{payload_agent}",
        event.session_id,
        event.agent_id.as_deref().unwrap_or("")
    )
}

pub(crate) fn prompt_context_key(payload: &Value) -> String {
    let mut context = payload.clone();
    if let Some(object) = context.as_object_mut() {
        for key in [
            "request_hash",
            "request_snapshot_blob_hash",
            "estimated_input_tokens",
            "contributing_event_ids",
        ] {
            object.remove(key);
        }
        let mut history_ids = Vec::new();
        for key in ["selected_sources", "omitted_sources"] {
            let Some(Value::Array(sources)) = object.get(key) else {
                continue;
            };
            let filtered = sources
                .iter()
                .filter(|source| {
                    let source_type = source.get("source_type").and_then(Value::as_str);
                    if matches!(source_type, Some("conversation" | "reasoning")) {
                        if let Some(id) = source.get("source_id").and_then(Value::as_str) {
                            history_ids.push(id.to_owned());
                        }
                        return false;
                    }
                    true
                })
                .cloned()
                .collect();
            object.insert(key.to_owned(), Value::Array(filtered));
        }
        if let Some(Value::Array(order)) = object.get("source_order") {
            let filtered = order
                .iter()
                .filter(|id| {
                    id.as_str()
                        .is_none_or(|id| !history_ids.iter().any(|history| history == id))
                })
                .cloned()
                .collect();
            object.insert("source_order".to_owned(), Value::Array(filtered));
        }
        if let Some(Value::Object(environment)) = object.get_mut("environment") {
            environment.remove("observed_at");
            if let Some(Value::Object(workspace)) = environment.get_mut("workspace") {
                workspace.remove("dirty");
                workspace.remove("changed_files");
            }
        }
    }
    canonical_json(&context)
}

pub(crate) fn prompt_context_changed(
    previous: &mut std::collections::HashMap<String, String>,
    event: &Event,
) -> bool {
    let scope = prompt_context_scope(event);
    let key = prompt_context_key(&event.payload);
    if previous.get(&scope) == Some(&key) {
        return false;
    }
    previous.insert(scope, key);
    true
}

fn canonical_json(value: &Value) -> String {
    serde_json::to_string(&canonicalize(value)).unwrap_or_else(|_| "null".to_owned())
}

fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(canonicalize).collect()),
        Value::Object(object) => {
            let mut keys: Vec<_> = object.keys().cloned().collect();
            keys.sort();
            let mut sorted = Map::new();
            for key in keys {
                if let Some(item) = object.get(&key) {
                    sorted.insert(key, canonicalize(item));
                }
            }
            Value::Object(sorted)
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{prompt_context_changed, prompt_context_key};
    use crate::api::Event;

    fn event(sequence: u64, payload: serde_json::Value) -> Event {
        Event {
            id: format!("event-{sequence}"),
            sequence,
            session_id: "session-one".to_owned(),
            run_id: Some("run-one".to_owned()),
            agent_id: Some("default".to_owned()),
            event_type: "context.compiled".to_owned(),
            schema_version: 1,
            created_at: "2026-09-02T18:00:00Z".to_owned(),
            causation_id: None,
            correlation_id: None,
            payload,
            blob_hash: None,
            payload_hash: format!("hash-{sequence}"),
            redaction_state: "clear".to_owned(),
        }
    }

    fn context_payload() -> serde_json::Value {
        json!({
            "provider": "codex",
            "model": "model-one",
            "agent_id": "default",
            "selected_sources": [{
                "source_id": "agent.default.instructions",
                "source_type": "agent",
                "content_hash": "instructions-v1",
                "selected_tokens": 100,
                "truncation": "none",
                "source_path": "/agents/default.md"
            }],
            "omitted_sources": [],
            "source_order": ["agent.default.instructions"]
        })
    }

    #[test]
    fn growing_history_and_request_totals_do_not_change_the_context_key() {
        let mut payload = context_payload();
        payload["environment"] = json!({"observed_at": "first", "workspace": {"cwd": "/project"}});
        let first = prompt_context_key(&payload);

        payload["request_hash"] = json!("new-request");
        payload["request_snapshot_blob_hash"] = json!("new-blob");
        payload["estimated_input_tokens"] = json!(5000);
        payload["contributing_event_ids"] = json!(["new-message"]);
        payload["selected_sources"] = json!([
            {
                "truncation": "none",
                "source_path": "/agents/default.md",
                "selected_tokens": 100,
                "content_hash": "instructions-v1",
                "source_type": "agent",
                "source_id": "agent.default.instructions"
            },
            {
                "source_id": "conversation.turn.one",
                "source_type": "conversation",
                "content_hash": "growing"
            }
        ]);
        payload["omitted_sources"] = json!([{
            "source_id": "reasoning.one",
            "source_type": "reasoning",
            "visibility": "audit"
        }]);
        payload["source_order"] = json!(["agent.default.instructions", "conversation.turn.one"]);
        payload["environment"] = json!({"workspace": {"cwd": "/project"}, "observed_at": "later"});

        assert_eq!(prompt_context_key(&payload), first);
    }

    #[test]
    fn unchanged_context_is_shown_once_across_calls() {
        let mut previous = std::collections::HashMap::new();
        let first = event(1, context_payload());
        let mut second_payload = context_payload();
        second_payload["request_hash"] = json!("new-request");
        second_payload["estimated_input_tokens"] = json!(5000);
        let second = event(2, second_payload);
        assert!(prompt_context_changed(&mut previous, &first));
        assert!(!prompt_context_changed(&mut previous, &second));
    }

    #[test]
    fn git_dirtiness_does_not_change_the_context_key() {
        let mut payload = context_payload();
        payload["environment"] = json!({
            "observed_at": "first",
            "workspace": {"cwd": "/project", "dirty": false, "changed_files": 0, "branch": "main"}
        });
        let first = prompt_context_key(&payload);
        payload["environment"] = json!({
            "observed_at": "later",
            "workspace": {"cwd": "/project", "dirty": true, "changed_files": 6, "branch": "main"}
        });
        assert_eq!(prompt_context_key(&payload), first);

        payload["environment"]["workspace"]["cwd"] = json!("/other");
        assert_ne!(prompt_context_key(&payload), first);
    }

    #[test]
    fn meaningful_source_changes_are_shown() {
        let mut previous = std::collections::HashMap::new();
        let first = event(1, context_payload());
        let mut changed = context_payload();
        changed["selected_sources"][0]["content_hash"] = json!("instructions-v2");
        let second = event(2, changed);
        let third = event(3, context_payload());
        assert!(prompt_context_changed(&mut previous, &first));
        assert!(prompt_context_changed(&mut previous, &second));
        assert!(prompt_context_changed(&mut previous, &third));
    }
}
