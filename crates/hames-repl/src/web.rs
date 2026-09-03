use std::env;

use anyhow::{Context, Result, bail};

use crate::api::{GatewayClient, PROTOCOL_VERSION};
use crate::local::{LocalPaths, ensure_gateway, ensure_search_setup};

pub async fn run(no_open: bool) -> Result<()> {
    let paths = LocalPaths::resolve()?;
    ensure_search_setup(&paths, false)?;
    ensure_gateway(&paths).await?;

    let client = GatewayClient::from_paths(&paths)?;
    let health = client.health().await?;
    if health.protocol_version != PROTOCOL_VERSION {
        bail!(
            "gateway protocol {} is incompatible with client protocol {}",
            health.protocol_version,
            PROTOCOL_VERSION
        );
    }
    let working_directory = env::current_dir()?.canonicalize()?;
    let launch = client
        .web_launch(
            working_directory
                .to_str()
                .context("working directory is not valid UTF-8")?,
        )
        .await?;

    if no_open {
        println!("Open {}", launch.url);
    } else if webbrowser::open(&launch.url).is_err() {
        eprintln!("Warning: could not open a browser; visit {}", launch.url);
    }
    Ok(())
}
