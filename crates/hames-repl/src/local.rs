use std::env;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, bail};
use crossterm::event::{self, Event, KeyCode};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, Paragraph};
use ratatui::{Frame, Terminal};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProviderBackend {
    LlamaCpp,
    Ollama,
    OpenAi,
    Codex,
}

impl ProviderBackend {
    fn profile_id(self) -> &'static str {
        match self {
            Self::LlamaCpp => "llama_cpp",
            Self::Ollama => "ollama",
            Self::OpenAi => "openai",
            Self::Codex => "codex",
        }
    }

    fn display_name(self) -> &'static str {
        match self {
            Self::LlamaCpp => "llama.cpp",
            Self::Ollama => "Ollama",
            Self::OpenAi => "OpenAI API",
            Self::Codex => "Codex / ChatGPT subscription",
        }
    }
}

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

pub fn run_setup(
    paths: &LocalPaths,
    requested_provider: Option<ProviderBackend>,
    fresh: bool,
) -> Result<()> {
    fs::create_dir_all(&paths.root)
        .with_context(|| format!("failed to create {}", paths.root.display()))?;
    let interactive = io::stdin().is_terminal() && io::stdout().is_terminal();
    let existing = configured_provider_backends(paths)?;
    let wizard = if requested_provider.is_none() && interactive {
        let Some(result) = setup_wizard(
            paths.config.exists(),
            paths.root.join("services/search/state.json").exists(),
            fresh,
            &existing,
        )?
        else {
            println!("Setup cancelled.");
            return Ok(());
        };
        result
    } else {
        SetupWizardResult {
            reset: fresh,
            providers: requested_provider.into_iter().collect(),
            web_search: None,
        }
    };
    if wizard.reset {
        write_config(paths, &toml::Value::Table(toml::map::Map::new()))?;
        println!("─ Started with a fresh Hames config");
    }

    if !wizard.providers.is_empty() {
        println!("─ Configuring provider backends");
    }
    for provider in wizard.providers {
        configure_provider(paths, provider)?;
        match provider {
            ProviderBackend::OpenAi if env::var_os("OPENAI_API_KEY").is_none() => {
                println!("  ○ OpenAI API · configured · OPENAI_API_KEY still required");
            }
            ProviderBackend::Codex => match ensure_codex_login(interactive)? {
                CodexLogin::Existing => {
                    println!("  ✓ Codex / ChatGPT subscription · using existing sign-in");
                }
                CodexLogin::Completed => {
                    println!("  ✓ Codex / ChatGPT subscription · sign-in completed");
                }
                CodexLogin::Required => {
                    println!("  ○ Codex / ChatGPT subscription · run `codex login`");
                }
            },
            _ => println!("  ✓ {}", provider.display_name()),
        }
    }
    if let Some(enabled) = wizard.web_search {
        println!("─ Configuring web search");
        run_backend([
            "search",
            "setup",
            if enabled { "--enable" } else { "--disable" },
            "--json",
        ])?;
    }
    println!("✓ Hames setup complete");
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct SetupWizardResult {
    reset: bool,
    providers: Vec<ProviderBackend>,
    web_search: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SetupWizardPage {
    Existing,
    Providers,
}

fn setup_wizard(
    config_exists: bool,
    search_configured: bool,
    fresh: bool,
    existing: &[ProviderBackend],
) -> Result<Option<SetupWizardResult>> {
    enable_raw_mode().context("failed to enter setup terminal mode")?;
    let mut output = io::stdout();
    if let Err(error) = execute!(output, EnterAlternateScreen) {
        let _ = disable_raw_mode();
        return Err(error).context("failed to open setup screen");
    }
    let backend = CrosstermBackend::new(output);
    let mut terminal = Terminal::new(backend).context("failed to create setup screen")?;
    let result = setup_wizard_loop(
        &mut terminal,
        config_exists,
        search_configured,
        fresh,
        existing,
    );
    let _ = disable_raw_mode();
    let _ = execute!(terminal.backend_mut(), LeaveAlternateScreen);
    let _ = terminal.show_cursor();
    result
}

fn setup_wizard_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    config_exists: bool,
    search_configured: bool,
    fresh: bool,
    existing: &[ProviderBackend],
) -> Result<Option<SetupWizardResult>> {
    let mut page = if config_exists && !fresh {
        SetupWizardPage::Existing
    } else {
        SetupWizardPage::Providers
    };
    let mut reset = fresh;
    let mut selected_row = 0_usize;
    let mut selected = [false; 4];
    for provider in existing {
        selected[provider_index(*provider)] = true;
    }
    if existing.is_empty() {
        selected[provider_index(ProviderBackend::LlamaCpp)] = true;
    }
    let show_web_search = !search_configured;
    let mut web_search = true;
    loop {
        terminal.draw(|frame| {
            let area = setup_area(frame.area());
            frame.render_widget(Clear, area);
            match page {
                SetupWizardPage::Existing => render_existing_setup(frame, area, selected_row),
                SetupWizardPage::Providers => render_provider_setup(
                    frame,
                    area,
                    selected_row,
                    &selected,
                    existing,
                    show_web_search,
                    web_search,
                ),
            }
        })?;
        let Event::Key(key) = event::read().context("failed to read setup input")? else {
            continue;
        };
        match page {
            SetupWizardPage::Existing => match key.code {
                KeyCode::Up => selected_row = selected_row.checked_sub(1).unwrap_or(2),
                KeyCode::Down => selected_row = (selected_row + 1) % 3,
                KeyCode::Esc => return Ok(None),
                KeyCode::Enter if selected_row == 0 => {
                    page = SetupWizardPage::Providers;
                    selected_row = 0;
                }
                KeyCode::Enter if selected_row == 1 => {
                    reset = true;
                    selected = [true, false, false, false];
                    page = SetupWizardPage::Providers;
                    selected_row = 0;
                }
                KeyCode::Enter => return Ok(None),
                _ => {}
            },
            SetupWizardPage::Providers => {
                let row_count = 4 + usize::from(show_web_search);
                match key.code {
                    KeyCode::Up => {
                        selected_row = selected_row.checked_sub(1).unwrap_or(row_count - 1)
                    }
                    KeyCode::Down => selected_row = (selected_row + 1) % row_count,
                    KeyCode::Char(' ') if selected_row < 4 => {
                        selected[selected_row] = !selected[selected_row]
                    }
                    KeyCode::Char(' ') => web_search = !web_search,
                    KeyCode::Esc => return Ok(None),
                    KeyCode::Enter => {
                        let providers = [
                            ProviderBackend::LlamaCpp,
                            ProviderBackend::Ollama,
                            ProviderBackend::OpenAi,
                            ProviderBackend::Codex,
                        ]
                        .into_iter()
                        .enumerate()
                        .filter_map(|(index, provider)| selected[index].then_some(provider))
                        .collect();
                        return Ok(Some(SetupWizardResult {
                            reset,
                            providers,
                            web_search: show_web_search.then_some(web_search),
                        }));
                    }
                    _ => {}
                }
            }
        }
    }
}

fn configured_provider_backends(paths: &LocalPaths) -> Result<Vec<ProviderBackend>> {
    if !paths.config.exists() {
        return Ok(Vec::new());
    }
    let config = paths.config_toml()?;
    let Some(providers) = config.get("providers").and_then(toml::Value::as_table) else {
        return Ok(Vec::new());
    };
    let mut result = Vec::new();
    for provider in [
        ProviderBackend::LlamaCpp,
        ProviderBackend::Ollama,
        ProviderBackend::OpenAi,
        ProviderBackend::Codex,
    ] {
        let legacy_llama =
            provider == ProviderBackend::LlamaCpp && providers.contains_key("llamacpp");
        if providers.contains_key(provider.profile_id()) || legacy_llama {
            result.push(provider);
        }
    }
    Ok(result)
}

fn provider_index(provider: ProviderBackend) -> usize {
    match provider {
        ProviderBackend::LlamaCpp => 0,
        ProviderBackend::Ollama => 1,
        ProviderBackend::OpenAi => 2,
        ProviderBackend::Codex => 3,
    }
}

fn setup_area(area: Rect) -> Rect {
    let width = area.width.min(76).max(36);
    let height = area.height.min(18).max(12);
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(area.height.saturating_sub(height) / 2),
            Constraint::Length(height),
            Constraint::Min(0),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Length(area.width.saturating_sub(width) / 2),
            Constraint::Length(width),
            Constraint::Min(0),
        ])
        .split(vertical[1])[1]
}

fn setup_block() -> Block<'static> {
    Block::default()
        .title(Line::from(vec![
            Span::styled("─ ", Style::default().fg(Color::Rgb(86, 94, 108))),
            Span::styled(
                "Hames setup",
                Style::default()
                    .fg(Color::Rgb(220, 224, 232))
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(" ─", Style::default().fg(Color::Rgb(86, 94, 108))),
        ]))
        .borders(Borders::TOP | Borders::BOTTOM)
        .border_style(Style::default().fg(Color::Rgb(86, 94, 108)))
}

fn render_existing_setup(frame: &mut Frame<'_>, area: Rect, selected: usize) {
    let inner = setup_block().inner(area);
    frame.render_widget(setup_block(), area);
    let rows = [
        ("Edit current", "preserve the existing config"),
        ("Start fresh", "replace the existing config"),
        ("Cancel", "leave everything unchanged"),
    ];
    let items = rows
        .into_iter()
        .enumerate()
        .map(|(index, (label, detail))| {
            ListItem::new(Line::from(vec![
                Span::styled(
                    if index == selected {
                        "  ● "
                    } else {
                        "  ○ "
                    },
                    Style::default().fg(Color::Rgb(156, 164, 178)),
                ),
                Span::styled(label, Style::default().fg(Color::Rgb(220, 224, 232))),
                Span::styled(
                    format!("  {detail}"),
                    Style::default().fg(Color::Rgb(112, 120, 134)),
                ),
            ]))
            .style(if index == selected {
                Style::default().bg(Color::Rgb(42, 46, 54))
            } else {
                Style::default()
            })
        })
        .collect::<Vec<_>>();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(3),
            Constraint::Length(2),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled(
                "  An existing Hames configuration was found.",
                Style::default().fg(Color::Rgb(220, 224, 232)),
            )),
        ]),
        chunks[0],
    );
    frame.render_widget(List::new(items), chunks[1]);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled("  ↑↓", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(" choose · ", Style::default().fg(Color::Rgb(112, 120, 134))),
            Span::styled("Enter", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(
                " continue · ",
                Style::default().fg(Color::Rgb(112, 120, 134)),
            ),
            Span::styled("Esc", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(" cancel", Style::default().fg(Color::Rgb(112, 120, 134))),
        ])),
        chunks[2],
    );
}

fn render_provider_setup(
    frame: &mut Frame<'_>,
    area: Rect,
    selected_row: usize,
    selected: &[bool; 4],
    existing: &[ProviderBackend],
    show_web_search: bool,
    web_search: bool,
) {
    let block = setup_block();
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let providers = [
        ProviderBackend::LlamaCpp,
        ProviderBackend::Ollama,
        ProviderBackend::OpenAi,
        ProviderBackend::Codex,
    ];
    let mut items = providers
        .into_iter()
        .enumerate()
        .map(|(index, provider)| {
            let detail = if existing.contains(&provider) {
                "already configured"
            } else {
                match provider {
                    ProviderBackend::LlamaCpp | ProviderBackend::Ollama => "local backend",
                    ProviderBackend::OpenAi => "API key",
                    ProviderBackend::Codex => "ChatGPT subscription",
                }
            };
            setup_check_item(
                selected[index],
                provider.display_name(),
                detail,
                selected_row == index,
            )
        })
        .collect::<Vec<_>>();
    if show_web_search {
        items.push(setup_check_item(
            web_search,
            "Private web search",
            "local SearXNG",
            selected_row == 4,
        ));
    }
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(2),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled(
                "  Select everything Hames should configure.",
                Style::default().fg(Color::Rgb(220, 224, 232)),
            )),
        ]),
        chunks[0],
    );
    frame.render_widget(List::new(items), chunks[1]);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled("  ↑↓", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(
                " navigate · ",
                Style::default().fg(Color::Rgb(112, 120, 134)),
            ),
            Span::styled("Space", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(" toggle · ", Style::default().fg(Color::Rgb(112, 120, 134))),
            Span::styled("Enter", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(
                " configure · ",
                Style::default().fg(Color::Rgb(112, 120, 134)),
            ),
            Span::styled("Esc", Style::default().fg(Color::Rgb(220, 224, 232))),
            Span::styled(" cancel", Style::default().fg(Color::Rgb(112, 120, 134))),
        ])),
        chunks[2],
    );
}

fn setup_check_item(
    checked: bool,
    label: &'static str,
    detail: &'static str,
    focused: bool,
) -> ListItem<'static> {
    ListItem::new(Line::from(vec![
        Span::styled(
            if checked { "  ☑ " } else { "  ☐ " },
            Style::default().fg(Color::Rgb(156, 164, 178)),
        ),
        Span::styled(label, Style::default().fg(Color::Rgb(220, 224, 232))),
        Span::styled(
            format!("  {detail}"),
            Style::default().fg(Color::Rgb(112, 120, 134)),
        ),
    ]))
    .style(if focused {
        Style::default().bg(Color::Rgb(42, 46, 54))
    } else {
        Style::default()
    })
}

fn configure_provider(paths: &LocalPaths, provider: ProviderBackend) -> Result<()> {
    let mut config = if paths.config.exists() {
        paths.config_toml()?
    } else {
        toml::Value::Table(toml::map::Map::new())
    };
    let root = config
        .as_table_mut()
        .context("Hames config.toml must contain a TOML table")?;
    let providers = root
        .entry("providers")
        .or_insert_with(|| toml::Value::Table(toml::map::Map::new()))
        .as_table_mut()
        .context("Hames providers config must be a TOML table")?;
    let profile = providers
        .entry(provider.profile_id())
        .or_insert_with(|| toml::Value::Table(toml::map::Map::new()))
        .as_table_mut()
        .context("Hames provider profile must be a TOML table")?;
    let (adapter, base_url) = match provider {
        ProviderBackend::LlamaCpp => ("llama_cpp", "http://127.0.0.1:8080"),
        ProviderBackend::Ollama => ("ollama", "http://127.0.0.1:11434"),
        ProviderBackend::OpenAi => ("openai", "https://api.openai.com/v1"),
        ProviderBackend::Codex => ("codex", "app-server://codex"),
    };
    profile.insert(
        "adapter".to_owned(),
        toml::Value::String(adapter.to_owned()),
    );
    profile
        .entry("base_url")
        .or_insert_with(|| toml::Value::String(base_url.to_owned()));
    if provider == ProviderBackend::OpenAi {
        profile.insert(
            "api_key_env".to_owned(),
            toml::Value::String("OPENAI_API_KEY".to_owned()),
        );
        profile
            .entry("supported_reasoning_efforts")
            .or_insert_with(|| {
                toml::Value::Array(
                    ["low", "medium", "high"]
                        .into_iter()
                        .map(|value| toml::Value::String(value.to_owned()))
                        .collect(),
                )
            });
    }
    write_config(paths, &config)
}

fn write_config(paths: &LocalPaths, config: &toml::Value) -> Result<()> {
    let serialized = toml::to_string_pretty(config)?;
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&paths.config)
        .with_context(|| format!("failed to write {}", paths.config.display()))?;
    file.write_all(serialized.as_bytes())?;
    file.sync_all()?;
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CodexLogin {
    Existing,
    Completed,
    Required,
}

fn ensure_codex_login(interactive: bool) -> Result<CodexLogin> {
    let logged_in = Command::new("codex")
        .args(["login", "status"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    if logged_in {
        return Ok(CodexLogin::Existing);
    }
    if !interactive {
        return Ok(CodexLogin::Required);
    }
    let status = Command::new("codex")
        .arg("login")
        .status()
        .context("failed to start `codex login`; install Codex CLI first")?;
    if !status.success() {
        bail!("Codex sign-in did not complete");
    }
    Ok(CodexLogin::Completed)
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

    use super::{LocalPaths, ProviderBackend, configure_provider, normalize_provider};

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

    #[test]
    fn provider_setup_preserves_existing_config_and_adds_cloud_backends() {
        let paths = temporary_paths("provider-setup");
        fs::create_dir_all(&paths.root).unwrap();
        fs::write(
            &paths.config,
            "[runtime]\ndefault_provider = \"llama_cpp\"\n\n[providers.llama_cpp]\nbase_url = \"http://router:8080\"\n",
        )
        .unwrap();

        configure_provider(&paths, ProviderBackend::OpenAi).unwrap();
        let config = paths.config_toml().unwrap();
        assert_eq!(
            config["providers"]["llama_cpp"]["base_url"].as_str(),
            Some("http://router:8080")
        );
        assert_eq!(
            config["providers"]["openai"]["api_key_env"].as_str(),
            Some("OPENAI_API_KEY")
        );
        fs::remove_dir_all(&paths.root).unwrap();
    }
}
