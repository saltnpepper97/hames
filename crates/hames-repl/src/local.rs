use std::env;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

#[derive(Clone, Debug)]
pub struct LocalPaths {
    pub root: PathBuf,
    pub token: PathBuf,
    pub history: PathBuf,
    pub config: PathBuf,
    pub preferences: PathBuf,
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
            preferences: root.join("ui.toml"),
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

    pub fn configured_theme(&self) -> Result<String> {
        if !self.preferences.exists() {
            return Ok("hames".to_owned());
        }
        let raw = fs::read_to_string(&self.preferences)
            .with_context(|| format!("failed to read {}", self.preferences.display()))?;
        let preferences: toml::Value = toml::from_str(&raw).context("invalid Hames ui.toml")?;
        Ok(preferences
            .get("theme")
            .and_then(toml::Value::as_str)
            .unwrap_or("hames")
            .to_owned())
    }

    pub fn write_theme(&self, theme: &str) -> Result<()> {
        if !matches!(theme, "hames" | "terminal") {
            bail!("unknown Hames theme: {theme}");
        }
        fs::create_dir_all(&self.root)
            .with_context(|| format!("failed to create {}", self.root.display()))?;
        let mut preferences = if self.preferences.exists() {
            let raw = fs::read_to_string(&self.preferences)
                .with_context(|| format!("failed to read {}", self.preferences.display()))?;
            toml::from_str(&raw).context("invalid Hames ui.toml")?
        } else {
            toml::Value::Table(toml::map::Map::new())
        };
        preferences
            .as_table_mut()
            .context("Hames ui.toml must contain a TOML table")?
            .insert("theme".to_owned(), toml::Value::String(theme.to_owned()));
        let serialized = toml::to_string_pretty(&preferences)?;
        let mut options = OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&self.preferences)
            .with_context(|| format!("failed to write {}", self.preferences.display()))?;
        file.write_all(serialized.as_bytes())?;
        file.sync_all()?;
        Ok(())
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

pub fn ensure_search_setup(paths: &LocalPaths, force: bool) -> Result<()> {
    let state = paths.root.join("services/search/state.json");
    if state.exists() && !force {
        return Ok(());
    }
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Ok(());
    }
    println!("Hames can set up private web search using a local SearXNG container.");
    println!("Search queries and fetched URLs will be sent to public web services.");
    print!("Enable web search? [Y/n] ");
    io::stdout().flush()?;
    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let enabled = !matches!(answer.trim().to_ascii_lowercase().as_str(), "n" | "no");
    run_backend([
        "search",
        "setup",
        if enabled { "--enable" } else { "--disable" },
        "--json",
    ])
}

pub fn write_private_export(path: &Path, content: &str, force: bool) -> Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create(true);
    if force {
        options.truncate(true);
    } else {
        options.create_new(true);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).with_context(|| {
        format!(
            "failed to create export {}; use --force to overwrite",
            path.display()
        )
    })?;
    file.write_all(content.as_bytes())?;
    file.sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
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
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::{LocalPaths, normalize_provider};

    fn temporary_paths(label: &str) -> LocalPaths {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("hames-{label}-{nonce}"));
        LocalPaths {
            token: root.join("runtime/gateway.token"),
            history: root.join("repl-history"),
            config: root.join("config.toml"),
            preferences: root.join("ui.toml"),
            root,
        }
    }

    #[test]
    fn default_gateway_url_is_loopback() {
        let paths = LocalPaths {
            root: "/tmp/example".into(),
            token: "/tmp/example/token".into(),
            history: "/tmp/example/history".into(),
            config: "/tmp/example/missing.toml".into(),
            preferences: "/tmp/example/ui.toml".into(),
        };
        assert_eq!(paths.gateway_url().unwrap(), "http://127.0.0.1:7411");
    }

    #[test]
    fn legacy_llamacpp_name_is_normalized() {
        assert_eq!(normalize_provider("llamacpp"), "llama_cpp");
        assert_eq!(normalize_provider("ollama"), "ollama");
    }

    #[test]
    fn theme_is_persisted_as_a_global_ui_preference() {
        let paths = temporary_paths("theme");
        assert_eq!(paths.configured_theme().unwrap(), "hames");

        paths.write_theme("terminal").unwrap();
        assert_eq!(paths.configured_theme().unwrap(), "terminal");
        assert!(paths.preferences.starts_with(&paths.root));

        paths.write_theme("hames").unwrap();
        assert_eq!(paths.configured_theme().unwrap(), "hames");
        fs::remove_dir_all(&paths.root).unwrap();
    }
}
