# Using aurral-tools

All tools print their help when run without arguments. Run them from the
folder they are in; each one finds its config file next to itself.

## Config files

Live config files hold secrets. Create them from the `.example` templates and
keep them readable only by you; the tools refuse to read a key or password
from a config that other users can read.

| File | Used by | Holds |
| --- | --- | --- |
| `aurral-tools.cfg` | `cancel-stuck-downloads.py`, `approve-reviews.py`, `rescan-library.py`, `unmonitor.py` | aurral URL and API key |
| `get-deezer-arl.cfg` | `get-deezer-arl.py` | Deezer email/password (optional), deemix URL, timeout |

```bash
cp aurral-tools.cfg.example aurral-tools.cfg
chmod 600 aurral-tools.cfg
```

The API key is in aurral under **Settings > General > API access**. It gives
admin access. After rotating it (**Rotate API key**), put the new key in
`aurral-tools.cfg`.

## Deezer sign-in for deemix: `get-deezer-arl.py`

deemix needs a Deezer ARL (sign-in cookie). This script opens Deezer's login
page in a real Chrome window, signs in (or lets you sign in), reads the ARL
and sends it to deemix's `/api/loginArl`, which checks it and saves it.

```bash
./get-deezer-arl.py --login                     # sign in, send ARL to deemix (URL from the config)
./get-deezer-arl.py --login --timeout 120       # give up after 2 minutes
./get-deezer-arl.py --login -c other.cfg        # use another config file
```

- With `[deezer] email` and `password` in the config, it fills the form and
  clicks Log in. Without them, you sign in yourself in the window.
- It never solves a CAPTCHA. If Deezer shows one, run it by hand and solve it.
- Without a deemix URL in the config or `--deemix-url`, it prints the ARL.
- The first run creates its own Python environment with Playwright in
  `~/.cache/get-deezer-arl/venv`.
- Deezer shows no login form to headless browsers. From cron, give the
  browser a virtual display:

  ```bash
  0 4 * * 0 /usr/bin/xvfb-run -a /path/to/get-deezer-arl.py --login >> ~/.cache/get-deezer-arl/cron.log 2>&1
  ```

## Keeping the download queue moving

Two helpers work around problems seen with deemix as aurral's source:

- Some deemix downloads stay **Downloading** after deemix has finished
  ([lklynet/aurral#1447](https://github.com/lklynet/aurral/issues/1447)).
- Downloads held for **Review** wait for someone to approve them.

### Start both

```bash
./start-queue-helpers.sh
```

This starts both scripts in `--watch` mode, detached from the terminal. Each
one stops by itself once aurral has nothing left to do (two checks in a row
with nothing Queued, Searching or Downloading). They are not restarted after
a reboot and do not wake up for new work after they stop: run the script
again after adding a large request. A watcher that is already running is
left alone (a lock prevents a second copy).

Logs:

- `~/.cache/aurral-stuck-downloads/watch.log`
- `~/.cache/aurral-approve-reviews/watch.log`

To stop them early:

```bash
pkill -f 'cancel-stuck-downloads.py --watch'
pkill -f 'approve-reviews.py --watch'
```

### `cancel-stuck-downloads.py`

Cancels jobs shown as **Downloading** for longer than the limit. A job is
cancelled only if it was also Downloading on the previous check, so one odd
reading never cancels anything.

```bash
./cancel-stuck-downloads.py --once              # one check
./cancel-stuck-downloads.py --watch             # every 4 minutes until the queue is done
./cancel-stuck-downloads.py --once --dry-run    # show what would be cancelled
./cancel-stuck-downloads.py --watch -m 15 -i 5  # 15-minute limit, check every 5 minutes
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--once` / `-w`, `--watch` | (required) | one check, or repeat until done |
| `-m`, `--max-minutes` | 10 | cancel jobs Downloading longer than this |
| `-i`, `--interval` | 4 | minutes between checks with `--watch` |
| `-n`, `--dry-run` | off | report only |
| `-c`, `--config` | `aurral-tools.cfg` | config file |

### `approve-reviews.py`

Approves downloads held for **Review**, in queue order, but only while few
downloads are running.

- If the downloaded file is gone (`Staging file no longer exists`), it
  **denies** the item instead, so aurral searches for the track again.
- Items that can be neither approved nor denied are skipped. Skips are kept
  per download attempt, because aurral reuses a job's ID when it searches
  again; a track that comes back after a new search is tried again.

```bash
./approve-reviews.py --once                     # one check
./approve-reviews.py --watch                    # every 5 minutes until done
./approve-reviews.py --once --dry-run           # show what would be approved
./approve-reviews.py --once -k 10 -a 99         # approve up to 10 now, however busy
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--once` / `-w`, `--watch` | (required) | one check, or repeat until done |
| `-a`, `--max-active` | 3 | approve only while at most this many downloads run |
| `-k`, `--count` | 4 | approve up to this many per check |
| `-i`, `--interval` | 5 | minutes between checks with `--watch` |
| `-n`, `--dry-run` | off | report only |
| `-c`, `--config` | `aurral-tools.cfg` | config file |

`aurral_api.py` holds the code the aurral tools share (config, API calls,
logging); it is not run on its own.

## Rescanning aurral's library: `rescan-library.py`

Starts a library scan, the same as **Library > Refresh library**.

```bash
./rescan-library.py --quick           # new, changed and removed files
./rescan-library.py --full            # read every file's tags again (after retagging)
./rescan-library.py --quick --wait    # wait until the scan finishes
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-q`, `--quick` / `-f`, `--full` | (required) | which scan |
| `-w`, `--wait` | off | wait for the scan to finish and report its result |
| `-t`, `--timeout` | 30 | minutes to wait with `--wait` |
| `-c`, `--config` | `aurral-tools.cfg` | config file |

## Stop monitoring an artist or its albums: `unmonitor.py`

Searches aurral's library artists by name (part of the name is enough, case
does not matter) and lists the matches with their monitoring state.

- Unmonitoring the **artist** stops aurral adding new albums for it.
- Unmonitoring its **albums** also unmonitors their tracks, so aurral's
  "search again for missing tracks" leaves them alone. Do this before moving
  an artist's files out of aurral's Downloads Folder, for example to retag
  them with Picard and move them into another library.

Files and library entries are kept either way.

```bash
./unmonitor.py kris --list            # just show matches
./unmonitor.py kris                   # pick a number, then artist / albums / both
./unmonitor.py kris -s 2 -a -b        # no prompts: match 2, artist and albums
./unmonitor.py kris -s 2 -b --dry-run # show which albums would change
```

| Option | Meaning |
| --- | --- |
| `NAME` | artist name or part of it |
| `-l`, `--list` | only list the matches |
| `-s`, `--select N` | pick match N without a prompt (needs `-a` and/or `-b`) |
| `-a`, `--artist` | unmonitor the artist |
| `-b`, `--albums` | unmonitor all its monitored albums |
| `-n`, `--dry-run` | show what would change |
| `-c`, `--config` | config file (default `aurral-tools.cfg`) |

## Removing MusicBrainz tags: `strip-musicbrainz-tags.py`

Removes MusicBrainz tags (MP3 `MusicBrainz …` frames and the MusicBrainz
recording ID, M4A `MusicBrainz …` tags, FLAC/Ogg `musicbrainz_…`) and keeps
every other tag, including aurral's `AURRAL_IDS`. Before changing any file
it saves every removed value to
`~/.cache/strip-musicbrainz-tags/removed-<date-time>.json`.

```bash
./strip-musicbrainz-tags.py --dry-run /path/to/music   # count only
./strip-musicbrainz-tags.py /path/to/music             # remove
```

aurral matches music by MusicBrainz tags first and by names second, and it
writes these tags again on new downloads and on **Clean up Library**. Without
them, aurral may link an artist to a different MusicBrainz artist with the
same name; fix that with the artist's **Edit MusicBrainz ID**.

## Adding ReplayGain tags: `tag-replaygain.py`

Players such as MPD can even out volume between songs, but only for files
with ReplayGain tags. This runs [rsgain](https://github.com/complexlogic/rsgain)
on each folder and writes track and album gain at ReplayGain 2.0 loudness
(-18 LUFS). Album gain is worked out per folder, so keep each album in its
own folder.

By default it:

- skips files that already have ReplayGain tags, so later runs only scan new
  downloads
- keeps each file's modified time, so players that sort by date added and
  library scans that look for changed files are not disturbed
- keeps each MP3's ID3 version

```bash
./tag-replaygain.py /path/to/music                 # track + album gain
./tag-replaygain.py -t /path/to/music              # track gain only
./tag-replaygain.py -a /path/to/music              # re-tag every file
./tag-replaygain.py -m 8 /path/one /path/two       # 8 files at a time
```

| Option | Meaning |
| --- | --- |
| `-m N`, `--threads N` | Files to scan at the same time (default 4). |
| `-t`, `--track-only` | Write track gain only, no album gain. |
| `-a`, `--all-files` | Also re-tag files that already have ReplayGain tags. |
| `-T`, `--update-times` | Let tagging update each file's modified time. |
| `-r PATH`, `--rsgain PATH` | rsgain program to run (default: `rsgain` on `PATH`). |

Only ReplayGain tags are written. MPD needs a database update afterwards to
read the new tags, and its ReplayGain setting (`replaygain` in `mpd.conf`)
chooses between the track and album values.

## Tips for aurral with deemix

- **Download sources** (Settings > Downloads): keep deemix at a lower
  priority number than yt-dlp. yt-dlp's YouTube Music searches can time out
  after 15 seconds each, which slows every download that reaches it.
- **Download concurrency** is at most 3 in aurral. It is set through
  `PUT /api/playlists/worker/settings`, not `/api/settings`
  ([lklynet/aurral#1443](https://github.com/lklynet/aurral/issues/1443)).
- Queued items labelled "Soulseek" are jobs not yet given a source, even with
  Soulseek disabled
  ([lklynet/aurral#1444](https://github.com/lklynet/aurral/issues/1444)).
- Review items cannot be cancelled; approve or deny them.
