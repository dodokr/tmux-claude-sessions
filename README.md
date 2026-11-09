# tmux-claude-sessions

Search every [Claude Code](https://claude.com/claude-code) session on your machine — by title,
by what was said, or by what it was *about* — and resume it in a new tmux window.

`claude --resume` only lists sessions of the current directory, and titles are often useless
("Untitled", or a summary of the first prompt). `claude-sessions` indexes all transcripts in
`~/.claude/projects` and gives you one fzf picker over them:

- **Keyword search as you type** — SQLite FTS5 with BM25 ranking, stemming, accent-insensitive
  (`cestovanie` finds `cestovánie`), prefix match on the word you are typing.
- **Semantic search on demand** (`Ctrl-S`) — a local [ollama](https://ollama.com) embedding
  model finds "the bluetooth toy car thing" even if the session never said those words. Results
  are fused with the keyword ranking (reciprocal rank fusion).
- **Preview** of the conversation — only what you and Claude wrote, no tool output — opened at
  the best-matching exchange, with your search terms highlighted.
- **Enter resumes** the session in a new tmux window in the session's project folder. If the
  session is already open in a pane, Enter jumps there instead of opening a second copy (needs
  tmux-claude-tasks, see below).
- **No daemon.** A Claude Code `SessionEnd` hook and the picker itself keep the index fresh;
  only changed transcripts are re-read, and only new text is embedded.

## Requirements

- Python ≥ 3.11 (standard library only; uses numpy for faster semantic search if installed)
- A recent [fzf](https://github.com/junegunn/fzf) (uses `--gutter`, `--with-shell`; tested with 0.74)
- tmux ≥ 3.3 for the popup (works outside tmux too: Enter then resumes in the current terminal)
- Optional, for semantic search: [ollama](https://ollama.com) with an embedding model:

  ```sh
  ollama pull embeddinggemma   # ~600 MB, multilingual
  ```

- Optional: [tmux-claude-tasks](https://github.com/dodokr/tmux-claude-tasks), a status board of
  the Claude sessions running in tmux right now. `claude-sessions` works fully without it; with
  it installed you also get the green `●` for open sessions, Enter jumping to an open session's
  pane instead of resuming a second copy, and `Ctrl-T` / `s` to switch between the two pickers.

## Install

1. **Put the script on your `PATH`:**

   ```sh
   git clone https://github.com/dodokr/tmux-claude-sessions.git
   ln -s "$PWD/tmux-claude-sessions/claude-sessions" ~/.local/bin/claude-sessions
   ```

2. **Build the index** (prints progress; the first embedding pass is the slow part, see
   [Performance](#performance) — you can use keyword search meanwhile):

   ```sh
   claude-sessions index
   ```

3. **Add the hook** from [`examples/claude-settings.json`](examples/claude-settings.json) to the
   `SessionEnd` hooks in `~/.claude/settings.json`, so each finished session is indexed in the
   background.

4. **Bind a key in tmux** (see [`examples/tmux.conf`](examples/tmux.conf)):

   ```tmux
   bind C-f display-popup -E -w 90% -h 85% "~/.local/bin/claude-sessions"
   ```

## Usage

Press your binding (`prefix + C-f` above) and type.

| Key | Action |
| --- | --- |
| *typing* | keyword search (BM25); empty query lists sessions by last activity |
| `Ctrl-S` | semantic + keyword search for the current query (prompt turns into `≈`) |
| `Enter` | resume in a new tmux window, or jump to the pane where it is open |
| `↑` / `↓`, `Ctrl-J` / `Ctrl-K` | move |
| `Shift-↑` / `Shift-↓`, `Ctrl-F` / `Ctrl-B` | scroll the preview |
| `Ctrl-/` | toggle the preview |
| `Ctrl-Y` | copy the session id |
| `Ctrl-T` | switch to `claude-tasks` (if installed) |
| `Esc` | close |

`claude-sessions --here` limits the picker to sessions started in or below the current
directory. A green `●` marks sessions that are open in a tmux pane right now (needs
tmux-claude-tasks). Titles come from `/rename`, then Claude's own title; sessions without either
show their first prompt in grey italics.

### Commands

```text
claude-sessions [--here]            open the picker (meant for: tmux display-popup -E)
claude-sessions search [-s] QUERY   print matching sessions; -s adds semantic ranking
claude-sessions preview ID [N [Q]]  print a session's conversation from exchange N
claude-sessions open ID             resume a session in a new tmux window (or jump to it)
claude-sessions index [-q]          update the index: text, then embeddings via ollama
claude-sessions hook                Claude Code hook: update the index in the background
claude-sessions status              show what is indexed
```

### Configuration

| Variable | Default | |
| --- | --- | --- |
| `CLAUDE_SESSIONS_RESUME` | `claude --resume {id}` | command typed into the new window, e.g. `claude --resume {id} --dangerously-skip-permissions` |
| `CLAUDE_SESSIONS_MODEL` | `embeddinggemma` | ollama embedding model; `none` disables semantic search |
| `OLLAMA_HOST` | `localhost:11434` | |
| `CLAUDE_SESSIONS_DIR` | `${XDG_STATE_HOME:-~/.local/state}/claude-sessions` | where the index lives |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | where Claude Code keeps `projects/` |
| `CLAUDE_TASKS_DIR` | `${XDG_STATE_HOME:-~/.local/state}/claude-tasks` | tmux-claude-tasks state, for live sessions |

The resume command is typed into your interactive shell, so shell functions and abbreviations
work, and the window stays open after Claude exits. Changing the model re-embeds everything on
the next `index`.

## How it works

- **What is indexed.** Each transcript is split into *exchanges*: one prompt you typed plus
  Claude's text replies to it. Tool calls and results, thinking, system reminders, compaction
  summaries, slash-command output and subagent transcripts are left out. The title, project path
  and branch are indexed too. Sessions with no prompt and `claude -p` runs are hidden.
- **Keyword search** is an FTS5 table (`porter unicode61 remove_diacritics 2`) queried with all
  words OR-ed together, ranked by BM25 with your prompts weighted above Claude's replies and the
  title above both. A session ranks by its best exchange.
- **Semantic search** embeds each exchange (long ones as overlapping ~1500-character pieces)
  with EmbeddingGemma's document prompt, embeds the query with its search prompt, and takes a
  brute-force cosine over all pieces — a few thousand vectors need no vector database. Vectors
  are stored by hash of their input text, so re-reading a changed transcript re-embeds only the
  new exchanges.
- **Freshness.** Transcripts are re-read when their size or mtime changes. "How long ago" uses
  the last message's timestamp, not the file's mtime, so syncing or copying transcripts between
  machines doesn't make old sessions look recent.

## Performance

Measured on an i7-13700H (CPU only) with ~130 sessions / 1.5 MB of conversation text:

| | |
| --- | --- |
| keyword search, list, preview | ~0.1 s per call (mostly Python startup) |
| semantic search | ~0.5 s (+ a few seconds if ollama has to load the model) |
| first full embedding pass | ~20 min (≈ 350 tokens/s), in the background |
| later updates | seconds — only new exchanges are embedded |
| index size | ~10 MB |

The picker loads the embedding model in the background when it opens, so `Ctrl-S` is usually
warm. While a large embedding pass is running, a semantic search waits for the current batch
(a few seconds).

## Development

```sh
python3 -m unittest discover tests   # synthetic transcripts, a fake ollama and a fake tmux
```

The tests never read your real sessions or call a real model.

## License

[MIT](LICENSE)
