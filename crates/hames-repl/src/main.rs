mod api;
mod local;
mod repl;

use anyhow::Result;
use clap::{Parser, Subcommand};

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
}

#[derive(Clone, Debug, Subcommand)]
enum GatewayAction {
    Start,
    Stop,
    Status,
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
        None => repl::run().await,
    }
}
