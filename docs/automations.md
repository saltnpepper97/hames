# Automations

Open **Automations** (the calendar-clock tab) and choose **Create automation**.
Enter a task, choose an agent, then select a schedule, local time, and timezone.
Review the task before enabling it; **Save paused** keeps a draft without running it.
You can also ask an agent to prepare a schedule in chat. The `automation_create`
tool creates a paused draft and links to this review screen. Give an explicit time
and timezone; for “every morning,” the agent should ask which time you prefer.

## Projects and connections

A workspace is optional. **General** runs without selecting a project. Hames keeps
its task files in a separate directory per automation, beside its private state
root: `~/.hames-automation-files/<automation-id>` by default. These directories are
not registered as project workspaces. Regular Web chats keep their existing
workspace behavior. Selecting a project requires that workspace to be trusted.

The selected agent supplies its tools and account connections. Scheduling a mail
check does not itself connect a mail account, or authorize sending replies.
Connect the required provider and tools before enabling the task. Missing access
appears as a run failure or an explanation in the run's chat.

## Runs and recovery

Each attempt gets its own conversation. The task page shows run history and links
to those chats. **Run now** works even while the schedule is paused. A task never
runs two attempts concurrently. Delete removes the schedule and history, while
keeping its conversations.

Schedules support once, daily, or selected weekdays, using an IANA timezone such
as `America/Halifax`. A skipped clock time moves forward to the first valid time;
a repeated clock time runs once. After sleep or downtime, catch-up runs once, not
once per missed occurrence. With catch-up disabled, occurrences more than five
minutes late are skipped. The gateway checks schedules every ten seconds.

Pending runs survive gateway restarts. A run already executing when the gateway
stops is reconciled against its terminal event; otherwise it is marked interrupted
for review rather than automatically repeating potentially completed side effects.
Optional retries apply only to explicitly retryable failures while the schedule
remains enabled, with two minutes between attempts and at most two retries.
Cancellation is never retried. One-time schedules disable themselves when claimed.

## Notifications

On Linux, Hames uses `notify-send` and a running desktop notification service such
as Mako. The gateway must have the desktop session's D-Bus environment. It checks
service availability automatically; no gateway restart is needed just to start
Mako. Notifications contain a task title and generic status, with an action to open
the run's chat. Private result text stays in Hames.

When native notifications are unavailable, Web can show browser notifications
while open, after permission is granted. Remote browsers use their own notification
permission. Set notifications per task to results and failures, failures only, or
off. The scheduler requires the gateway to be running; it does not wake the computer.
