mod api;
mod local;
mod repl;

use std::env;
use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use serde::Serialize;

use crate::api::GatewayClient;
use crate::local::{LocalPaths, write_private_export};

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
    /// Manage portable agent capsules.
    Agent {
        #[command(subcommand)]
        action: AgentAction,
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
enum AgentAction {
    List {
        #[arg(long)]
        json: bool,
    },
    Show {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Create {
        id: String,
        #[arg(long)]
        name: Option<String>,
        #[arg(long, value_enum, default_value_t = AgentAuthority::Standard)]
        authority: AgentAuthority,
        #[arg(long)]
        json: bool,
    },
    Validate {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Delete {
        id: String,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum AgentAuthority {
    Standard,
    ReadOnly,
}

impl AgentAuthority {
    fn as_str(self) -> &'static str {
        match self {
            Self::Standard => "standard",
            Self::ReadOnly => "read_only",
        }
    }
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
    /// Export a derived, read-only audit transcript.
    Export {
        id: String,
        #[arg(long, value_enum, default_value_t = AuditFormat::Markdown)]
        format: AuditFormat,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        force: bool,
    },
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum AuditFormat {
    Markdown,
    Jsonl,
}

impl AuditFormat {
    fn as_str(self) -> &'static str {
        match self {
            Self::Markdown => "markdown",
            Self::Jsonl => "jsonl",
        }
    }
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
        Some(Command::Agent { action }) => run_agent_command(action).await,
        Some(Command::Event { action }) => run_event_command(action).await,
        None => repl::run().await,
    }
}

async fn run_agent_command(action: AgentAction) -> Result<()> {
    let (_, client) = connected_client().await?;
    match action {
        AgentAction::List { json } => {
            let agents = client.agents().await?;
            if json {
                print_json(&agents)
            } else {
                for agent in agents {
                    println!("{:<20} {:<10} {}", agent.id, agent.authority, agent.name);
                }
                Ok(())
            }
        }
        AgentAction::Show { id, json } => {
            let agent = client.agent(&id).await?;
            if json {
                print_json(&agent)
            } else {
                println!(
                    "{}  {}\n{}",
                    agent.agent.id, agent.agent.authority, agent.instructions
                );
                Ok(())
            }
        }
        AgentAction::Create {
            id,
            name,
            authority,
            json,
        } => {
            let default_name = id.clone();
            let agent = client
                .create_agent(
                    &id,
                    name.as_deref().unwrap_or(&default_name),
                    authority.as_str(),
                )
                .await?;
            if json {
                print_json(&agent)
            } else {
                println!("created agent {}", agent.agent.id);
                Ok(())
            }
        }
        AgentAction::Validate { id, json } => {
            let agent = client.validate_agent(&id).await?;
            if json {
                print_json(&agent)
            } else {
                println!("{} is valid", agent.agent.id);
                Ok(())
            }
        }
        AgentAction::Delete { id, json } => {
            let result = client.retire_agent(&id).await?;
            if json {
                print_json(&result)
            } else {
                println!("retired agent {id}");
                Ok(())
            }
        }
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
                .create_session(
                    &cwd.to_string_lossy(),
                    "default",
                    &provider,
                    &model,
                    &reasoning,
                )
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
            let session = client.fork_session(&id, at.as_deref(), None).await?;
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
        SessionAction::Export {
            id,
            format,
            output,
            force,
        } => {
            let transcript = client.transcript(&id, format.as_str()).await?;
            write_private_export(&output, &transcript, force)?;
            println!(
                "exported {} audit transcript to {}",
                format.as_str(),
                output.display()
            );
            Ok(())
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
