**Opened by:** foundry run [{{ run_id }}]({{ run_url }}), a change to foundry itself.

## What it changes

{{ change.summary }}

## Why

{{ change.blocker }}

## Testing

{% for step in change.testing %}
- {{ step }}
{% else %}
- Not tested by the agent.
{% endfor %}
- **Ask the agent:** write in the [run's chat]({{ run_url }}); it picks up in the same conversation.

## Review checklist

- [ ] The change is small and about the blocker above.
- [ ] Nothing in it bypasses a review, a check, or the branch rules.
- [ ] No secrets or account details in the diff.

Once merged, the agent calls `restart` and its run comes back on the new code.
