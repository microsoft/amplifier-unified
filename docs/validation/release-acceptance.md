# Platform and first-use acceptance

Status: checklist prepared; platform and human results are still pending.
Run against the exact candidate artifact supplied by the release owner. Record
its version, commit, artifact checksum and source link before starting. Follow
the [setup instructions](../../README.md#set-up-unified-on-your-computer) for
that candidate, rather than installing a moving main branch. Use a fresh test
profile/account and synthetic work; keep any working installation separate.

## Platform checks

Run once on **Windows with WSL and a Windows browser**, and once on **macOS with
the backend running locally**. A Mac browser connected to Linux does not qualify
the macOS backend.

1. Install and start from the published instructions. Open the app, sign in,
   connect an AI account and send a simple request. Record any extra steps or
   help needed, plus the time until the first visible response.
2. Attach a short sample document. Ask for a useful summary or action list,
   save the output, then reopen both the conversation and the saved output.
3. Leave a distinctive unsent draft. Reload, switch chats and return, then
   sleep/wake the computer. Confirm the draft survives. Temporarily disconnect
   the network and reconnect; confirm the app recovers without duplicate sends.
4. In a long synthetic chat, read an older message, switch away and return,
   then reload. Check that the reading position and navigation remain useful.
   Try keyboard navigation and reduced motion.
5. With work idle, restart only the test backend. Reopen the app and check the
   saved conversation, output and draft. If supplied an upgrade pair, update
   through Settings and repeat. Record restart and upgrade results separately.

Record **pass**, **fail**, or **not tried** for each step. Stop a failing scenario
long enough to capture its visible message and approximate timing; do not hide a
failure behind repeated retries. A successful retry is additional evidence.

## First-use observation

Give someone unfamiliar with the app only this task and the normal setup guide:

> Set up Amplifier, connect an AI account, and use a short sample document to
> make something useful for your work. Save the result and find it again.

Do not point out controls or coach the route. Record where they hesitate, what
they expect, any help requested, and whether they can find their result again.
Self-testing by someone familiar with the app is valuable platform evidence;
label it separately from an uncoached first-use observation.

## Result record

Use GitHub aliases or anonymous tester labels. Include platform/OS version,
processor architecture, browser/version, local versus remote backend, candidate
identity, results and rough timings. Exclude people's real names, machine names,
private addresses, personal file paths, account details and private document
content. Review diagnostics and screenshots before attaching them.

This checklist does not qualify physical voice interruption, Bluetooth/mobile
handoffs, original-history recovery or every provider. Record those separately;
do not infer them from successful chat or installation checks.
