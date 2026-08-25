mod activity;
mod api;
mod local;
mod repl;
mod style;
mod tui;

use std::env;
use std::fs;
use std::io::{self, IsTerminal};
use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand, ValueEnum};
use serde::Serialize;

use crate::api::GatewayClient;
use crate::local::{LocalPaths, write_private_export};

#[derive(Debug, Parser)]
#[command(name = "hames", version, about = "The Hames Rust REPL")]
struct Cli {
    /// Force the classic line-oriented REPL.
    #[arg(long, global = true)]
    repl: bool,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Configure Hames and its private local services.
    Setup {
        /// Configure one provider directly; omit for the state-aware setup flow.
        #[arg(value_enum)]
        provider: Option<SetupProvider>,
        /// Replace the current config before running setup.
        #[arg(long)]
        fresh: bool,
    },
    /// Open the full-screen terminal interface.
    Tui,
    /// Open the classic line-oriented REPL.
    Repl,
    /// Check the local Hames environment.
    Doctor,
    /// Control the persistent Python gateway.
    Gateway {
        #[command(subcommand)]
        action: GatewayAction,
    },
    /// Inspect and control private web search.
    Search {
        #[command(subcommand)]
        action: SearchAction,
    },
    /// Inspect and branch durable sessions.
    Session {
        #[command(subcommand)]
        action: SessionAction,
    },
    /// List, create, or retire portable agent capsules under ~/.hames/agents.
    Agent {
        #[command(subcommand)]
        action: AgentAction,
    },
    /// Inspect and control autonomous Skills.
    Skill {
        #[command(subcommand)]
        action: SkillAction,
    },
    /// Inspect, install, and enable isolated plugins.
    Plugin {
        #[command(subcommand)]
        action: PluginAction,
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
    Restart,
    Status,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum SetupProvider {
    LlamaCpp,
    Ollama,
    Openai,
    Codex,
}

#[derive(Clone, Debug, Subcommand)]
enum SearchAction {
    Status,
    Start,
    Stop,
    Restart,
    Update,
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
    /// Create a capsule. Pass --name; the directory id is slugged from it.
    Create {
        /// Display name (Researcher -> id researcher). Omit for hames-1, hames-2, ...
        #[arg(long)]
        name: Option<String>,
        /// Preset: standard (default) or read_only. Does not grant tools.
        #[arg(long, value_enum, default_value_t = AgentAuthority::Standard)]
        authority: AgentAuthority,
        /// Seed from an AGENT.md file instead of the default body.
        #[arg(long = "from")]
        from_path: Option<PathBuf>,
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

#[derive(Clone, Debug, Subcommand)]
enum SkillAction {
    List {
        session: String,
        #[arg(long, default_value = "")]
        query: String,
        #[arg(long)]
        json: bool,
    },
    Show {
        session: String,
        id: String,
        #[arg(long)]
        json: bool,
    },
    History {
        session: String,
        id: String,
        #[arg(long)]
        json: bool,
    },
    Jobs {
        session: String,
        #[arg(long)]
        json: bool,
    },
    Author {
        session: String,
        goal: String,
        #[arg(long, default_value = "workspace")]
        scope: String,
        #[arg(long)]
        json: bool,
    },
    Correct {
        session: String,
        id: String,
        goal: String,
        #[arg(long)]
        json: bool,
    },
    Retry {
        session: String,
        job: String,
        #[arg(long)]
        json: bool,
    },
    Pin {
        session: String,
        id: String,
    },
    Unpin {
        session: String,
        id: String,
    },
    Archive {
        session: String,
        id: String,
    },
    Restore {
        session: String,
        id: String,
    },
    Rollback {
        session: String,
        id: String,
    },
}

#[derive(Clone, Debug, Subcommand)]
enum PluginAction {
    Inspect {
        path: PathBuf,
        #[arg(long)]
        json: bool,
    },
    Install {
        path: PathBuf,
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
    Enable {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Disable {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Remove {
        id: String,
        #[arg(long)]
        json: bool,
    },
    Proposals {
        #[arg(long)]
        json: bool,
    },
    Proposal {
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
        Some(Command::Setup { provider, fresh }) => {
            let paths = LocalPaths::resolve()?;
            local::run_setup(
                &paths,
                provider.map(|value| match value {
                    SetupProvider::LlamaCpp => local::ProviderBackend::LlamaCpp,
                    SetupProvider::Ollama => local::ProviderBackend::Ollama,
                    SetupProvider::Openai => local::ProviderBackend::OpenAi,
                    SetupProvider::Codex => local::ProviderBackend::Codex,
                }),
                fresh,
            )
        }
        Some(Command::Tui) => tui::run().await,
        Some(Command::Repl) => repl::run().await,
        Some(Command::Doctor) => local::run_backend(["doctor", "--json"]),
        Some(Command::Gateway { action }) => {
            let paths = LocalPaths::resolve()?;
            if matches!(action, GatewayAction::Start | GatewayAction::Restart) {
                local::ensure_search_setup(&paths, false)?;
            }
            let action = match action {
                GatewayAction::Start => "start",
                GatewayAction::Stop => "stop",
                GatewayAction::Restart => "restart",
                GatewayAction::Status => "status",
            };
            local::run_gateway_action(action)
        }
        Some(Command::Search { action }) => {
            let action = match action {
                SearchAction::Status => "status",
                SearchAction::Start => "start",
                SearchAction::Stop => "stop",
                SearchAction::Restart => "restart",
                SearchAction::Update => "update",
            };
            local::run_backend(["search", action, "--json"])
        }
        Some(Command::Session { action }) => run_session_command(action).await,
        Some(Command::Agent { action }) => run_agent_command(action).await,
        Some(Command::Skill { action }) => run_skill_command(action).await,
        Some(Command::Plugin { action }) => run_plugin_command(action).await,
        Some(Command::Event { action }) => run_event_command(action).await,
        None if cli.repl || !io::stdin().is_terminal() || !io::stdout().is_terminal() => {
            repl::run().await
        }
        None => tui::run().await,
    }
}

async fn run_skill_command(action: SkillAction) -> Result<()> {
    let (_, client) = connected_client().await?;
    match action {
        SkillAction::List {
            session,
            query,
            json,
        } => {
            let skills = client.skills(&session, &query).await?;
            if json {
                print_json(&skills)
            } else {
                for skill in skills {
                    println!(
                        "{:<28} v{:<3} {:<10} {}",
                        skill.slug, skill.version, skill.scope, skill.description
                    );
                }
                Ok(())
            }
        }
        SkillAction::Show { session, id, json } => {
            let skill = client.skill(&session, &id).await?;
            if json {
                print_json(&skill)
            } else {
                println!("{} v{}\n{}", skill.slug, skill.version, skill.instructions);
                Ok(())
            }
        }
        SkillAction::History { session, id, json } => {
            let history = client.skill_history(&session, &id).await?;
            if json {
                print_json(&history)
            } else {
                for skill in history {
                    println!(
                        "{} v{}  {}  {}",
                        skill.slug, skill.version, skill.status, skill.content_hash
                    );
                }
                Ok(())
            }
        }
        SkillAction::Jobs { session, json } => {
            let jobs = client.skill_jobs(&session).await?;
            if json {
                print_json(&jobs)
            } else {
                for job in jobs {
                    println!(
                        "{}  {:<10} {:<12} {}",
                        job.id, job.kind, job.status, job.goal
                    );
                }
                Ok(())
            }
        }
        SkillAction::Author {
            session,
            goal,
            scope,
            json,
        } => {
            let job = client.author_skill(&session, &goal, &scope, None).await?;
            if json {
                print_json(&job)
            } else {
                println!("queued autonomous Skill job {}", job.id);
                Ok(())
            }
        }
        SkillAction::Correct {
            session,
            id,
            goal,
            json,
        } => {
            let current = client.skill(&session, &id).await?;
            let job = client
                .author_skill(&session, &goal, &current.scope, Some(&current.skill_id))
                .await?;
            if json {
                print_json(&job)
            } else {
                println!("queued autonomous Skill correction {}", job.id);
                Ok(())
            }
        }
        SkillAction::Retry { session, job, json } => {
            let job = client.retry_skill_job(&session, &job).await?;
            if json {
                print_json(&job)
            } else {
                println!("retry queued for {}", job.id);
                Ok(())
            }
        }
        SkillAction::Pin { session, id } => control_skill(&client, &session, &id, "pin").await,
        SkillAction::Unpin { session, id } => control_skill(&client, &session, &id, "unpin").await,
        SkillAction::Archive { session, id } => {
            control_skill(&client, &session, &id, "archive").await
        }
        SkillAction::Restore { session, id } => {
            control_skill(&client, &session, &id, "restore").await
        }
        SkillAction::Rollback { session, id } => {
            control_skill(&client, &session, &id, "rollback").await
        }
    }
}

async fn control_skill(
    client: &GatewayClient,
    session: &str,
    id: &str,
    action: &str,
) -> Result<()> {
    let skill = client.control_skill(session, id, action).await?;
    println!("{} v{} is {}", skill.slug, skill.version, skill.status);
    Ok(())
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
            name,
            authority,
            from_path,
            json,
        } => {
            let source = from_path
                .as_ref()
                .map(fs::read_to_string)
                .transpose()
                .with_context(|| {
                    format!(
                        "failed to read {}",
                        from_path
                            .as_ref()
                            .map_or(String::new(), |path| path.display().to_string())
                    )
                })?;
            let agent = client
                .create_agent(name.as_deref(), authority.as_str(), source.as_deref())
                .await?;
            if json {
                print_json(&agent)
            } else {
                println!("created agent {} ({})", agent.agent.id, agent.agent.name);
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

async fn run_plugin_command(action: PluginAction) -> Result<()> {
    let (_, client) = connected_client().await?;
    match action {
        PluginAction::Inspect { path, json } => {
            let inspected = client
                .inspect_plugin(&path.canonicalize()?.display().to_string())
                .await?;
            if json {
                print_json(&inspected)
            } else {
                println!(
                    "{}  v{}  {}",
                    inspected.id, inspected.version, inspected.fingerprint
                );
                if !inspected.permissions.is_empty() {
                    println!("permissions: {}", inspected.permissions.join(", "));
                }
                Ok(())
            }
        }
        PluginAction::Install { path, json } => {
            let plugin = client
                .install_plugin(&path.canonicalize()?.display().to_string())
                .await?;
            if json {
                print_json(&plugin)
            } else {
                println!("installed {} v{} (disabled)", plugin.id, plugin.version);
                Ok(())
            }
        }
        PluginAction::List { json } => {
            let plugins = client.plugins().await?;
            if json {
                print_json(&plugins)
            } else {
                if plugins.is_empty() {
                    println!("no plugins installed");
                    return Ok(());
                }
                for plugin in plugins {
                    let state = if plugin.running {
                        "running"
                    } else if plugin.enabled {
                        "enabled"
                    } else {
                        "disabled"
                    };
                    println!("{:<20} {:<10} {}", plugin.id, state, plugin.name);
                }
                Ok(())
            }
        }
        PluginAction::Show { id, json } => {
            let plugin = client.plugin(&id).await?;
            if json {
                print_json(&plugin)
            } else {
                print_plugin(&plugin);
                Ok(())
            }
        }
        PluginAction::Enable { id, json } => {
            let plugin = client.enable_plugin(&id).await?;
            if json {
                print_json(&plugin)
            } else {
                println!("enabled {}", plugin.id);
                if !plugin.warning.is_empty() {
                    println!("warning: {}", plugin.warning);
                }
                Ok(())
            }
        }
        PluginAction::Disable { id, json } => {
            let plugin = client.disable_plugin(&id).await?;
            if json {
                print_json(&plugin)
            } else {
                println!("disabled {}", plugin.id);
                Ok(())
            }
        }
        PluginAction::Remove { id, json } => {
            let result = client.remove_plugin(&id).await?;
            if json {
                print_json(&result)
            } else {
                println!("removed plugin {id}");
                Ok(())
            }
        }
        PluginAction::Proposals { json } => {
            let proposals = client.plugin_proposals().await?;
            if json {
                print_json(&proposals)
            } else {
                if proposals.is_empty() {
                    println!("no plugin proposals");
                    return Ok(());
                }
                for proposal in proposals {
                    println!(
                        "{}  {:<10} {:<20} {}",
                        &proposal.id[..8.min(proposal.id.len())],
                        proposal.status,
                        proposal.plugin_id,
                        proposal.package_path
                    );
                }
                Ok(())
            }
        }
        PluginAction::Proposal { id, json } => {
            let proposal = client.plugin_proposal(&id).await?;
            if json {
                print_json(&proposal)
            } else {
                println!(
                    "{}  {}  {}\n{}",
                    proposal.id, proposal.status, proposal.plugin_id, proposal.package_path
                );
                if !proposal.permissions.is_empty() {
                    println!("permissions: {}", proposal.permissions.join(", "));
                }
                Ok(())
            }
        }
    }
}

fn print_plugin(plugin: &api::Plugin) {
    println!(
        "{}  {}  v{}",
        plugin.id,
        if plugin.running {
            "running"
        } else if plugin.enabled {
            "enabled"
        } else {
            "disabled"
        },
        plugin.version
    );
    if !plugin.permissions.is_empty() {
        println!("permissions: {}", plugin.permissions.join(", "));
    }
    if !plugin.tools.is_empty() {
        println!("tools: {}", plugin.tools.join(", "));
    }
    if !plugin.warning.is_empty() {
        println!("warning: {}", plugin.warning);
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
            let reasoning = reasoning.unwrap_or_default();
            let cwd = env::current_dir()?.canonicalize()?;
            let session = client
                .create_session(&cwd.to_string_lossy(), "", &provider, &model, &reasoning)
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
