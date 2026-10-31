# aurral-tools

Command-line helpers for [aurral](https://github.com/lklynet/aurral) with
[deemix](https://github.com/bambanah/deemix) as its download source.

| Tool | What it does |
| --- | --- |
| `get-deezer-arl.py` | Signs in to Deezer in a real browser, reads the ARL and hands it to deemix. Can run from cron. |
| `cancel-stuck-downloads.py` | Cancels aurral downloads that stay "Downloading" after deemix has finished ([lklynet/aurral#1447](https://github.com/lklynet/aurral/issues/1447)). |
| `approve-reviews.py` | Approves downloads held for review while few downloads are running; denies ones whose file is gone so aurral searches again. |
| `start-queue-helpers.sh` | Runs the two tools above in the background until aurral's queue is done. |
| `rescan-library.py` | Starts a quick or full library scan. |
| `unmonitor.py` | Finds artists by name and stops monitoring the artist and/or its albums. |
| `strip-musicbrainz-tags.py` | Removes MusicBrainz tags from music files, saving the removed values first. |

## Requirements

- aurral reachable over HTTP, and its API key (Settings > General > API access)
- deemix web server, for `get-deezer-arl.py`
- Python 3.10 or newer
- `get-deezer-arl.py` and `strip-musicbrainz-tags.py` install their own
  Python packages (Playwright, mutagen) into a private environment under
  `~/.cache/` on first run. `get-deezer-arl.py` also uses an installed
  Google Chrome (or downloads Chromium) and, from cron, `xvfb-run`.

## Quick start

```bash
git clone https://github.com/bonelifer/aurral-tools.git
cd aurral-tools
cp aurral-tools.cfg.example aurral-tools.cfg
chmod 600 aurral-tools.cfg
# put aurral's URL and API key in aurral-tools.cfg
./rescan-library.py --quick --wait
./start-queue-helpers.sh
```

Every tool prints its help when run without arguments. Config files hold
secrets: the tools refuse to read a key or password from a config that other
users can read.

See [docs/usage.md](docs/usage.md) for every tool's options and examples.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/aurral-tools/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/aurral-tools/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- [aurral](https://github.com/lklynet/aurral) by lklynet and
  [deemix](https://github.com/bambanah/deemix), which these tools work with.
- Code, tests, and documentation written with [Claude](https://www.anthropic.com/claude).

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
