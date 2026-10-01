# vestaboard-commitgraph

A [FiestaBoard](https://fiestaboard.app) plugin that renders a GitHub user's
contribution (commit) graph on a Vestaboard.

Every tile on the board is one day. A day with activity is shown as a green
tile; a day without activity uses the "inactive" color (blank by default).

| Board    | Size    | Days shown |
|----------|---------|------------|
| Note     | 15 × 3  | 45         |
| Flagship | 22 × 6  | 132        |

Note arrays work too: they show one day per tile, whatever their size.

## Layout

The most recent day (today) is in the **bottom-right** corner. Going back in
time, each column fills **bottom to top**, then continues at the bottom of the
column to its left. The oldest day ends up in the **top-left** corner.

Neither board has seven rows, so days are not grouped into week columns the
way github.com shows them.

```
Note (day number = days ago)

 44 41 38 35 32 29 26 23 20 17 14 11  8  5  2
 43 40 37 34 31 28 25 22 19 16 13 10  7  4  1
 42 39 36 33 30 27 24 21 18 15 12  9  6  3  0   <- today
```

## Installation

In FiestaBoard, install the plugin from this git repository:

```bash
curl -X POST http://localhost:4420/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"repository": "https://github.com/jessehouwing/vestaboard-commitgraph"}'
```

FiestaBoard takes the plugin id from the repository name, giving
`vestaboard_commitgraph`. That matches the `id` in `manifest.json`.

## Settings

| Setting | Description |
|---------|-------------|
| **GitHub Username** | *(required)* The user whose graph is shown. |
| **GitHub Token** | *(optional)* A personal access token. When set, the plugin uses the GitHub **GraphQL API** contribution calendar, the same data as the graph on your GitHub profile. Without a token it falls back to the public **REST events API**. That API only returns public events from the last 90 days (300 events at most), and unauthenticated requests are limited to 60 per hour. |
| **Active Day Color** | Tile color for days with activity. Default: `green`. |
| **Inactive Day Color** | Tile color for days without activity. Default: `blank`, which leaves the board's own flap color. |
| **Refresh Interval** | How often to query GitHub, in seconds. Default: 3600, minimum: 300. |

A classic token without scopes, or a fine-grained token with only public
read access, is enough. To include private contributions, enable *"Private
contributions"* in your GitHub profile settings.

## Template variables

| Variable | Description |
|----------|-------------|
| `{{vestaboard_commitgraph.commit_graph}}` | The full-board graph. Use it on its own in the first line with wrapping enabled. |
| `{{vestaboard_commitgraph.username}}` | The configured GitHub username |
| `{{vestaboard_commitgraph.days}}` | Number of days shown |
| `{{vestaboard_commitgraph.active_days}}` | Days with at least one contribution |
| `{{vestaboard_commitgraph.contributions}}` | Total contributions in the shown days |
| `{{vestaboard_commitgraph.streak}}` | Consecutive active days ending today |

## Development

Tests run against the FiestaBoard core, the same way CI does (see
`.github/workflows/ci.yml`):

```bash
git clone https://github.com/Fiestaboard/FiestaBoard fiestaboard-core
pip install -r fiestaboard-core/requirements.txt pytest pytest-cov
mkdir -p plugins && touch plugins/__init__.py
ln -s .. plugins/vestaboard_commitgraph && ln -s . vestaboard_commitgraph
PYTHONPATH=.:fiestaboard-core BOARD_READ_WRITE_KEY=test_key pytest tests/
```
