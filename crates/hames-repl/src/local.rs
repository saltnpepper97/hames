use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

#[derive(Clone, Debug)]
pub struct LocalPaths {
    pub root: PathBuf,
    pub token: PathBuf,
    pub history: PathBuf,
    pub config: PathBuf,
}

impl LocalPaths {
    pub fn resolve() -> Result<Self> {
        let root = match env::var_os("HAMES_HOME") {
            Some(value) => PathBuf::from(value),
            None => {
                let home = env::var_os("HOME").context("HOME is not set")?;
                PathBuf::from(home).join(".hames")
            }
        };
        Ok(Self {
            token: root.join("runtime/gateway.token"),
            history: root.join("repl-history"),
            config: root.join("config.toml"),
            root,
        })
    }

    pub fn gateway_url(&self) -> Result<String> {
        let mut host = "127.0.0.1".to_owned();
        let mut port = 7411_u16;
        if self.config.exists() {
            let config = self.config_toml()?;
            if let Some(gateway) = config.get("gateway") {
                if let Some(value) = gateway.get("host").and_then(toml::Value::as_str) {
                    host = if value == "localhost" {
                        "127.0.0.1".to_owned()
                    } else {
                        value.to_owned()
                    };
                }
                if let Some(value) = gateway.get("port").and_then(toml::Value::as_integer) {
                    port =
                        u16::try_from(value).context("gateway port is outside the valid range")?;
                }
            }
        }
        if let Ok(value) = env::var("HAMES_GATEWAY__HOST") {
            host = if value == "localhost" {
                "127.0.0.1".to_owned()
            } else {
                value
            };
        }
        if let Ok(value) = env::var("HAMES_GATEWAY__PORT") {
            port = value.parse().context("HAMES_GATEWAY__PORT is invalid")?;
        }
        Ok(format!("http://{host}:{port}"))
    }

    pub fn configured_provider(&self) -> Result<String> {
        if let Ok(value) = env::var("HAMES_RUNTIME__DEFAULT_PROVIDER") {
            return Ok(normalize_provider(&value));
        }
        if let Some(value) = self.config_value(&["runtime", "default_provider"])? {
            return Ok(normalize_provider(&value));
        }
        if let Some(value) = self.config_value(&["active_provider"])? {
            return Ok(normalize_provider(&value));
        }
        Ok("llama_cpp".to_owned())
    }

    pub fn configured_model(&self, provider: &str) -> Result<String> {
        let key = format!("HAMES_PROVIDERS__{}__MODEL", provider.to_uppercase());
        if let Ok(value) = env::var(key) {
            return Ok(value);
        }
        if let Some(value) = self.config_value(&["providers", provider, "model"])? {
            return Ok(value);
        }
        Ok(self
            .legacy_provider_value(provider, "model")?
            .unwrap_or_default())
    }

    pub fn configured_reasoning(&self, provider: &str) -> Result<String> {
        let key = format!(
            "HAMES_PROVIDERS__{}__REASONING_EFFORT",
            provider.to_uppercase()
        );
        if let Ok(value) = env::var(key) {
            return Ok(value);
        }
        if let Some(value) = self.config_value(&["providers", provider, "reasoning_effort"])? {
            return Ok(value);
        }
        Ok(self
            .legacy_provider_value(provider, "reasoning_effort")?
            .unwrap_or_default())
    }

    fn legacy_provider_value(&self, provider: &str, field: &str) -> Result<Option<String>> {
        let legacy = match provider {
            "llama_cpp" => "llamacpp",
            value => value,
        };
        self.config_value(&["providers", legacy, field])
    }

    fn config_toml(&self) -> Result<toml::Value> {
        let raw = fs::read_to_string(&self.config)
            .with_context(|| format!("failed to read {}", self.config.display()))?;
        toml::from_str(&raw).context("invalid Hames config.toml")
    }

    fn config_value(&self, path: &[&str]) -> Result<Option<String>> {
        if !self.config.exists() {
            return Ok(None);
        }
        let value = self.config_toml()?;
        let mut current = &value;
        for component in path {
            let Some(next) = current.get(*component) else {
                return Ok(None);
            };
            current = next;
        }
        Ok(current.as_str().map(str::to_owned))
    }
}

fn normalize_provider(value: &str) -> String {
    match value {
        "llamacpp" => "llama_cpp".to_owned(),
        other => other.to_owned(),
    }
}

pub fn run_backend<const N: usize>(args: [&str; N]) -> Result<()> {
    let status = Command::new(backend_command())
        .args(args)
        .status()
        .context("failed to execute hamesd; install the Python backend or set HAMESD")?;
    if !status.success() {
        bail!("hamesd exited with {status}");
    }
    Ok(())
}

pub fn start_backend() -> Result<()> {
    run_backend(["start", "--json"])
}

fn backend_command() -> OsString {
    if let Some(command) = env::var_os("HAMESD") {
        return command;
    }
    let development = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin/hamesd");
    if development.exists() {
        development.into_os_string()
    } else {
        OsString::from("hamesd")
    }
}

#[cfg(test)]
mod tests {
    use super::{LocalPaths, normalize_provider};

    #[test]
    fn default_gateway_url_is_loopback() {
        let paths = LocalPaths {
            root: "/tmp/example".into(),
            token: "/tmp/example/token".into(),
            history: "/tmp/example/history".into(),
            config: "/tmp/example/missing.toml".into(),
        };
        assert_eq!(paths.gateway_url().unwrap(), "http://127.0.0.1:7411");
    }

    #[test]
    fn legacy_llamacpp_name_is_normalized() {
        assert_eq!(normalize_provider("llamacpp"), "llama_cpp");
        assert_eq!(normalize_provider("ollama"), "ollama");
    }
}
