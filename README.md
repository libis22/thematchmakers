# The Matchmakers 💘

A small chat UI for testing the `provider_matching_assistant` AgentCore Harness.
Internal demo only — no login, just a shared-secret header to keep the URL from
being wide open to the internet.

```
 Browser (Amplify-hosted SPA)
        |  fetch POST, x-matchmaker-secret header
        v
 Lambda Function URL  --->  Lambda (backend/app.py)
                                  |  boto3 invoke_harness()
                                  v
                       AgentCore Harness: provider_matching_assistant
                                  |  (Gateway -> tools Lambda -> Athena / KB / Location)
                                  v
                              reply text (markdown)
```

Nothing about the existing Harness, Gateway, tools Lambda, or Athena view changes.
This only adds a front door: one proxy Lambda and one static page.

## What's in here

- `backend/` — the proxy Lambda (`app.py`) and a SAM template (`template.yaml`)
  that deploys it behind a public Function URL.
- `frontend/` — `index.html`, a single self-contained page (no build step,
  no npm install). Loads `marked.js` from a CDN to render the Harness's
  markdown replies (tables, bold, links) the same way the console's test
  harness does.
- `amplify.yml` — tells AWS Amplify Hosting to serve `frontend/` as-is.

## 1. Deploy the backend

Prerequisites: [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html),
credentials for account `196337091137` / region `us-east-1`.

```bash
cd backend
sam build
sam deploy --guided
```

When prompted:

- **HarnessArn** — defaults to `provider_matching_assistant`'s ARN; accept it
  unless you're pointing at a different Harness.
- **SharedSecret** — make up any random string (e.g. `openssl rand -hex 16`).
  You'll paste this same value into the frontend in step 2.
- **AllowedOrigin** — leave as `*` for now. Once you know your Amplify URL
  (step 2), you can `sam deploy` again with `--parameter-overrides
  AllowedOrigin=https://main.xxxxxxxx.amplifyapp.com` to lock CORS down to it.

Note the **FunctionUrl** output when it finishes — you'll need it next.

**IAM note:** the template grants the proxy Lambda's own execution role
`bedrock-agentcore:InvokeHarness` on the Harness ARN. That's a guess at the
exact action name based on the `invoke_harness` API call — this service is
new enough that it might differ slightly. If `sam deploy` or the first chat
message fails with an `AccessDenied`, the error message will name the exact
action it wanted; update the `Policies` block in `template.yaml` with that
and redeploy.

## 2. Point the frontend at it

Open `frontend/index.html` and fill in the two constants near the top of the
`<script>` block:

```js
const API_URL = "https://xxxxxxxx.lambda-url.us-east-1.on.aws/"; // from step 1's output
const SHARED_SECRET = "the-same-random-string-you-used-above";
```

That's the only wiring needed — it's a static page, so there's no build step
to rerun.

## 3. Deploy the frontend to Amplify Hosting

Push this repo to GitHub (or GitLab/Bitbucket/CodeCommit), then:

1. AWS Console → **Amplify** → **Host a web app** → connect the repo/branch.
2. Amplify will pick up `amplify.yml` automatically (base directory `frontend/`).
3. Deploy. Amplify gives you a URL like `https://main.xxxxxxxx.amplifyapp.com`.

If you'd rather not connect a git provider for a one-off demo, `amplify
hosting` also supports a manual zip upload of the `frontend/` folder from the
console — same result, no git required.

(Optional) Go back and tighten the backend's CORS to this URL per the note
in step 1.

## Notes / known trade-offs

- **"Typing" effect is faked client-side.** The proxy Lambda drains the
  Harness's streamed response fully before replying (see the comment at the
  top of `app.py`), and the frontend animates the text onto the screen
  afterward. This was a deliberate simplicity trade-off — real token-by-token
  streaming through a Lambda Function URL needs either the
  [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) or
  a different compute target (e.g. App Runner). Worth doing later if the
  dead pause before replies feels bad in practice; not needed to try this out.
- **No tool-call trace in the UI.** The console's "Agent trace" panel is the
  console's own instrumentation — the `invoke_harness` stream only showed
  `contentBlockDelta` text events, so building an equivalent trace view would
  need checking what other event types come through the stream and rendering
  those too. Left out of v1.
- **Session ID lives in `sessionStorage`.** Each browser tab gets its own
  conversation with the Harness; closing the tab starts a fresh one next
  time. Swap for `localStorage` in `frontend/index.html` if you'd rather
  conversations persisted across a refresh/reopen.
- **`SHARED_SECRET` is visible in the page source** — it's a deterrent
  against a random person finding the URL, not real security. If this ever
  needs actual per-user access control, swap in a Cognito User Pool in
  front of Amplify and pass the Cognito ID token to the Lambda instead.
