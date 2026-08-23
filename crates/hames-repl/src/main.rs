mod api;
mod local;
mod repl;

use std::env;

use anyhow::Result;
use clap::{Parser, Subcommand};
use serde::Serialize;

use crate::api::GatewayClient;
use crate::local::LocalPaths;

#[derive(Debug, Parser)]
#[command(name = "hames", version, about = "The Hames Rust REPL")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Check the local Hames environment.
    Doctor,
    /// Control the persistent Python gateway.
    Gateway {
        #[command(subcommand)]
        action: GatewayAction,
    },
    /// Inspect and branch durable sessions.
    Session {
        #[command(subcommand)]
        action: SessionAction,
    },
    /// Inspect durable events.
    Event {
        #[command(subcommand)]
        action: EventAction,
    },
}

#[derive(Clone, Debug, Subcommand)]
enum GatewayAction {
    Start,
    Stop,
    Status,
}

#[derive(Clone, Debug, Subcommand)]
enum SessionAction {
    New {
        #[arg(long)]
        provider: Option<String>,
        #[arg(long)]
        model: Option<String>,
        #[arg(long)]
        reasoning: Option<String>,
        #[arg(long)]
        json: bool,
    },
    List {
        #[arg(long)]
        json: bool,
    },
    Show {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Fork {
        id: String,
        #[arg(long)]
        at: Option<String>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Clone, Debug, Subcommand)]
enum EventAction {
    Verify {
        id: String,
        #[arg(long)]
        json: bool,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Some(Command::Doctor) => local::run_backend(["doctor", "--json"]),
        Some(Command::Gateway { action }) => {
            let action = match action {
                GatewayAction::Start => "start",
                GatewayAction::Stop => "stop",
                GatewayAction::Status => "status",
            };
            local::run_backend([action, "--json"])
        }
        Some(Command::Session { action }) => run_session_command(action).await,
        Some(Command::Event { action }) => run_event_command(action).await,
        None => repl::run().await,
    }
}

async fn connected_client() -> Result<(LocalPaths, GatewayClient)> {
    let paths = LocalPaths::resolve()?;
    repl::ensure_gateway(&paths).await?;
    let client = GatewayClient::from_paths(&paths)?;
    Ok((paths, client))
}

fn print_json<T: Serialize>(value: &T) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

async fn run_session_command(action: SessionAction) -> Result<()> {
    let (paths, client) = connected_client().await?;
    match action {
        SessionAction::New {
            provider,
            model,
            reasoning,
            json,
        } => {
            let provider = provider.unwrap_or(paths.configured_provider()?);
            let model = model.unwrap_or(paths.configured_model(&provider)?);
            let reasoning = reasoning.unwrap_or(paths.configured_reasoning(&provider)?);
            let cwd = env::current_dir()?.canonicalize()?;
            let session = client
                .create_session(&cwd.to_string_lossy(), &provider, &model, &reasoning)
                .await?;
            if json {
                print_json(&session)
            } else {
                println!(
                    "{}  {} / {}  {}",
                    session.id,
                    session.provider,
                    session.model,
                    cwd.display()
                );
                Ok(())
            }
        }
        SessionAction::List { json } => {
            let sessions = client.sessions().await?;
            if json {
                print_json(&sessions)
            } else {
                for session in sessions {
                    println!(
                        "{}  {:<8}  {} / {}  {}",
                        session.id,
                        session.status,
                        session.provider,
                        session.model,
                        session
                            .title
                            .as_deref()
                            .unwrap_or(&session.working_directory)
                    );
                }
                Ok(())
            }
        }
        SessionAction::Show { id, json } => {
            let session = client.session(&id).await?;
            let history = client.history(&id).await?;
            if json {
                print_json(&serde_json::json!({"session": session, "history": history}))
            } else {
                println!(
                    "{}  {}  {} / {}\n{} events",
                    session.id,
                    session.status,
                    session.provider,
                    session.model,
                    history.len()
                );
                for event in history {
                    println!("{:>6}  {}  {}", event.sequence, event.id, event.event_type);
                }
                Ok(())
            }
        }
        SessionAction::Fork { id, at, json } => {
            let session = client.fork_session(&id, at.as_deref()).await?;
            if json {
                print_json(&session)
            } else {
                println!(
                    "{}  branch of {} at {}",
                    session.id,
                    session.parent_session_id.as_deref().unwrap_or("unknown"),
                    session.fork_event_id.as_deref().unwrap_or("unknown")
                );
                Ok(())
            }
        }
    }
}

async fn run_event_command(action: EventAction) -> Result<()> {
    let (_, client) = connected_client().await?;
    match action {
        EventAction::Verify { id, json } => {
            let result = client.verify_event(&id).await?;
            if json {
                print_json(&result)
            } else {
                println!("{}  verified  {}", result.event_id, result.payload_hash);
                Ok(())
            }
        }
    }
}
