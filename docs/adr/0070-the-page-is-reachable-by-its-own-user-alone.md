# ADR-0070: The page is reachable by its own user alone

**Status:** accepted · **Date:** 2026-10-27 · Builds on [ADR-0069](0069-a-page-drives-quackds-own-commands-over-json-lines.md) (a page's yes is a person's yes, which holds only while the page is reachable by its own user alone) · Implemented in `quackd/webui/launch.py`, `quackd/webui/app.py` (the token's check), `quackd/webui/state.py` (`Setup.keys`), `quackd/webui/catalog.py` (`key_for`, `session_environment`), `quackd/webui/jobs.py` (`_child_environment`), `quackd/dotenv_names.py` (`shell_environment`, `loaded`) and `quackd/cli.py` (`ui_cmd`, `robot add` and `robot edit --token-env`) · Documented in [docs/guides/web-ui.md](../guides/web-ui.md)

## Context

`quackd ui` serves a Streamlit page that starts runs, answers their questions and stops them, and
ADR-0069 lets a Yes clicked in it clear a dangerous verb and fly the arm under a VLA pilot, as a
person's answer. That is only true while the person clicking is the person the page belongs to.

Streamlit's own defaults do not see to that. With `server.address` unset it listens on every
interface the computer has, so a page started on a laptop in a café is on the café's network. It
collects usage statistics unless told not to, watches its script's files and reruns the page when
one changes, and shows a developer's toolbar. And the page has to work from a phone, which is a
second device on a network by definition.

Keys have a problem of their own. A page is the natural place to paste an API key, and every place
a key could go from there is a place it would outlive the page: a file, a command line any process
list shows, or the record a run writes, which keeps the command that started it.

## Decision

**Loopback unless told otherwise.** The page listens on `127.0.0.1`, which this computer alone
reaches, unless `--bind` names another address. Nothing about a run changes with the address.

**A token always, loopback included.** Every session must bring the page's token, in the address
it opens, before anything but the refusal is drawn. Loopback is not one person: another account on
the same computer reaches `127.0.0.1`, and so can a web page in your own browser that points a name
of its own at it, which Streamlit's check of a connection's origin against its host lets through.
Neither has the token. The browser `quackd ui` opens on this computer is handed it, so it costs
nothing there. The token is made once with `secrets.token_urlsafe(18)`, written into `ui.token` in
the quackd home through a file made readable by its owner alone before anything is written into
it, and is the same at every start, so a shortcut on a phone's home screen keeps working;
`--new-token` replaces it. It is compared as bytes in constant time, kept in the session once
given, and put back in the address on every run, since a page switch drops the address's query
and a refresh, or a phone whose session lapsed while it was locked, would come back without it.

**A `.env` chooses nothing about the page.** The page's token and its saved setup live in the home
the shell names, never one a project's `.env` names: a cloned project must not choose the token,
nor bring a setup with a policy server of its own in it. The runs the page starts get the shell's
environment alone and load the working folder's `.env` themselves, as a run from that folder's
terminal does, so they still tell a file's values from the shell's, which the settings a `.env`
may not decide are read by. The saved setup holds no key, and is written private all the same,
since an address or an extra body can carry a credential.

**The way there is printed with the token in it.** The launcher prints the address with the
token, and on any address but loopback each address this computer has on its networks, says that
anyone who has it can drive the robots, and draws a QR code of the first in the terminal, or saves
it, private as the token, as a picture where the terminal cannot draw it. The page shows the same
code in its sidebar. A page bound to one address is waited for, and opened, at that address.

**Streamlit is told who reaches the page.** The settings that decide it go on Streamlit's command
line, which outranks a `.streamlit/config.toml` and a variable a project could bring: the address,
CORS and XSRF protection on, no base path. The cookie secret, which Streamlit refuses on its
command line, is a new one at every start, set in its environment, which outranks a config file
too. Usage statistics are off, the file watcher is off, the toolbar is minimal and development
mode is off.

**A key typed into the page stays in its memory.** It is kept in the page process for as long as
the page runs, never in the saved setup, which holds no secret, and reaches a run through that
run's environment alone: never a file, never a command line, never a **Same as** line, so never the
record. The page shows where a key would come from, masked as `quackd doctor` masks it, in the
order a run finds it: the key typed into the page, then the shell's environment, then `.env`.

**A robot's token travels the same way.** `quackd robot add` and `robot edit` take
`--token-env` and `--host-token-env`, the name of a variable holding the token, so the page can
save a robot without putting a token on a command line, and no refusal quotes what was typed where
the name goes.

**A port that is taken is refused before anything is announced.** Streamlit reports it only after
the page has printed, and opened, an address another program answers.

## Why not

- **A token only off loopback.** That was the first design, and it left the page open to every
  account on the computer and to a web page that rebinds a name to `127.0.0.1`. The token costs a
  person on this computer nothing, since the browser `quackd ui` opens is handed it.
- **Streamlit's defaults.** Every interface, statistics on and a page that reloads when a file
  changes are right for a data app on a developer's machine and wrong for a page that drives a
  robot.
- **A login form, or Streamlit's own login.** Streamlit's login needs an identity provider. A token
  in the address is what a QR code can carry, and a phone can keep as a shortcut.
- **A new token at every start.** Safer for a token somebody saw, and it would break every
  shortcut at every restart. `--new-token` is there for the token somebody saw.
- **HTTPS by default.** It needs a certificate a phone trusts, and a self-signed one teaches people
  to click past the warning. Streamlit can serve it through its own settings, and quackd has no
  flag for it until somebody has tried it.
- **Writing a typed key into `.env`.** A file the page writes is a file every later run reads, and
  `.env` is the person's own to edit.
- **A public tunnel for the phone away from home.** Anyone who finds the address and the token
  could drive the robots from anywhere. The guide sends people to a private network such as
  Tailscale instead, bound to that network's address alone.

## Consequences

- The page is plain HTTP. On a Wi-Fi network the token, and any key typed into the page on a
  phone, cross the air unencrypted. The guide says so, and says to type keys at the computer.
- The token sits in the address, so a browser's history, a screenshot or a shared link carries it.
  `--new-token` is the remedy. Streamlit's own `URL:` line, printed above quackd's, carries no
  token and opens on the refusal.
- Streamlit hears nothing from `quackd ui` about how it is used.
- One job per robot is the page's own rule. A run on the same robot started from a terminal beside
  the page is not seen: quackd has no lock across processes.
- The phone's path has run in a headless browser made to behave as a phone, against the page
  bound to loopback, and the token's refusal, its place in the address across a page switch and a
  refresh have run in a browser. It has not yet run on a phone over a Wi-Fi network, nor through
  Tailscale.
