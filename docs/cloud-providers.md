# Cloud provider connections

Hames supports DeepSeek API, Z.ai API, and Z.ai Coding Plan as separate provider
choices, alongside Xiaomi MiMo API and Xiaomi MiMo Token Plan. In Web, open **Settings → Connections**, use **Connect provider** in the
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
hames setup mimo
hames setup mimo-token-plan
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
precedence over the saved key: `DEEPSEEK_API_KEY`, `ZAI_API_KEY`, `MIMO_API_KEY`, and `MIMO_TOKEN_PLAN_API_KEY`. A profile's
`api_key_env` can name a different variable for separate accounts. The gateway
process must receive the variable; setting it only in another terminal does not
change a running service. The local credential-file option avoids that issue.

## Endpoint separation

| Setup choice | Adapter/profile | Base URL |
| --- | --- | --- |
| Xiaomi MiMo API | `mimo` | `https://api.xiaomimimo.com/v1` |
| Xiaomi MiMo Token Plan | `mimo_token_plan` | `https://token-plan-cn.xiaomimimo.com/v1` |
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

## Xiaomi MiMo

MiMo uses Hames's Chat Completions harness for streamed text, thinking, tool
calls, tool results, and token/cache usage. Thinking is an On/Off toggle; MiMo
does not offer distinct low/high/max reasoning strengths. Original reasoning
is replayed across tool steps and turns. Requests use `max_completion_tokens`;
custom temperature is sent only when thinking is off.

Connect with your key in Settings → Connections or `/connect`. The API and
Token Plan have separate keys and profiles; Hames never switches between their
endpoints on failure. For headless configuration, the Token Plan profile uses
`MIMO_TOKEN_PLAN_API_KEY` so it can coexist with `MIMO_API_KEY` for ordinary API
billing. Custom endpoints and environment variable names remain configurable.

Models come from authenticated `/models` discovery, with no static fallback.
The setup preference is `mimo-v2.6-pro`; a successful connection selects an
actually returned model if that preference is unavailable. The current docs
also list `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`, `mimo-v2.5-pro`, and
`mimo-v2.5`. Account access still determines which models can be used.

Hames exposes image attachments for the documented multimodal models (V2.6,
V2.5, and V2 Omni). V2.5 Pro remains text-only. Known context limits are used
when discovery omits them (a conservative 1,000,000 tokens for V2.6 and
1,048,576 for V2.5); returned metadata and explicit configuration take precedence.
Dedicated ASR/TTS models are excluded from the agent model picker. Audio/video
input and speech synthesis are not part of Hames's current chat attachment API.

Documentation checked 2026-09-21:

- [MiMo API and Token Plan endpoints](https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call)
- [Chat Completions protocol](https://mimo.mi.com/docs/en-US/api/chat/openai-api)
- [Model discovery](https://mimo.mi.com/docs/en-US/api/model/list-models)
- [MiMo V2.6 capabilities](https://mimo.mi.com/docs/en-US/news/latest/v2-6)
- [V2.5 context configuration](https://mimo.mi.com/docs/en-US/tokenplan/integration/codex-configuration)
