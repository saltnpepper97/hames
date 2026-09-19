# DeepSeek and Z.ai connections

Hames supports DeepSeek API, Z.ai API, and Z.ai Coding Plan as separate provider
choices. In Web, open **Settings → Connections**, use **Connect provider** in the
model menu, or type `/connect`. In the TUI, use `/connect` or **Connect provider**
in the model menu. Both interfaces let you enter a hidden API key, test access,
see returned models, replace the key, and disconnect.

Web and TUI connections take effect immediately. Profiles are saved in
`~/.hames/connections.json`; keys are separate private files. Managed profiles
override profiles with the same ID in `config.toml`. Existing custom endpoints
and environment credentials are managed through their original configuration.
The displayed status reflects the last test in this gateway process; after a
restart, use Test to check access again. A documented fallback catalog does not
prove account access.

For terminal-only configuration without the interactive UI, setup also accepts:

```sh
hames setup deepseek
hames setup zai
hames setup zai-coding
```

Setup links to the provider's API-key page and accepts the key in a hidden local
prompt. Press Enter to configure the provider now and connect later. Keys are
saved under `~/.hames/credentials/` (or your selected Hames home), in files readable
only by your user. They are not written to the conversation or `config.toml`.
These are local credential files, not an encrypted OS keychain.

After changing provider configuration through `hames setup`, restart the gateway when its active work
has finished. Then use the Web or TUI model picker to probe the provider and
select a model. Saving a key does not itself prove the account can access a model.
Environment variables remain available for advanced/headless setups and take
precedence over the saved key: `DEEPSEEK_API_KEY` and `ZAI_API_KEY`. A profile's
`api_key_env` can name a different variable for separate accounts. The gateway
process must receive the variable; setting it only in another terminal does not
change a running service. The local credential-file option avoids that issue.

## Endpoint separation

| Setup choice | Adapter/profile | Base URL |
| --- | --- | --- |
| DeepSeek API | `deepseek` | `https://api.deepseek.com` |
| Z.ai API | `zai` | `https://api.z.ai/api/paas/v4` |
| Z.ai Coding Plan | `zai_coding` | `https://api.z.ai/api/coding/paas/v4` |

Coding Plan requests always use that profile's configured endpoint. Hames does
not retry a plan failure against the regular paid API. Account/model access and
Coding Plan tool eligibility are determined by Z.ai; endpoint support does not
assert that Hames is an officially approved subscription client. There is no
separate DeepSeek subscription endpoint configured here.

## Model discovery and reasoning

Hames calls the selected endpoint's authenticated `/models` route and uses the
returned model IDs. A configured default is not inserted into a successful live
model list. Authentication, quota and transport failures remain failures.

If a Z.ai endpoint explicitly returns 404/405 for `/models`, Hames offers its
small documented catalog with status `configured`, rather than claiming those
models were discovered on the account. A subsequent model request still requires
provider authorization. DeepSeek discovery has no static-list fallback.

The documentation checked on 2026-09-18 lists:

- DeepSeek: `deepseek-flash`, `deepseek-v4-pro`.
- Z.ai's current flagship/Coding Plan choices: `glm-5.3`, `glm-5.3-flash`.

These are documentation-based choices, not results from a connected account.
The standard Z.ai API can report additional models. Discovery metadata takes
precedence over known model context sizes; explicit profile context limits
remain authoritative.

Both adapters stream text, reasoning, tool calls and usage through Hames's shared
harness. The original returned reasoning is retained with replayed assistant
messages across tool steps and user turns, as required by these APIs. Compaction
still replaces complete historical exchanges with a continuity summary.

GLM-5.3 models require reasoning. Their model menus offer `low`, `high`, and `max`,
not Off. Internal summary requests asking for Off use the documented low effort
instead. Older GLM models expose an On/Off toggle. DeepSeek supports its thinking
toggle plus low/high/max effort. Provider errors are surfaced without silently
switching providers, models, endpoints, or billing modes.

## References

- [DeepSeek models](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek thinking and tool replay](https://api-docs.deepseek.com/guides/thinking_mode/)
- [Z.ai quick start](https://docs.z.ai/guides/overview/quick-start)
- [Z.ai Coding Plan endpoints and eligibility](https://docs.z.ai/devpack/faq)
- [Z.ai preserved thinking](https://docs.z.ai/guides/capabilities/thinking-mode)
- [GLM-5.3 reasoning controls](https://docs.z.ai/guides/llm/glm-5.3)
