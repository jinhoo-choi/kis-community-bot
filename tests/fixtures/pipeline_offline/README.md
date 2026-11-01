# Offline pipeline fixture

Frozen from commit `0bfde5284c8575ee3fe24c0f440da603de30a3ab` (2026-10-06).
The JSON files are byte-for-byte copies of that commit's `data/` files.

- Posts: 50; kinds: {'research': 13, 'flow': 32, 'disclosure': 4, 'policy': 1}.
- Market: 198 items; day: 2026-10-06.
- Listed-company map: 2627 entries.

These are public-news/market fixtures, not current operating outputs or customer data.
Do not refresh them from daily production. Update deliberately only when a test contract changes.
The pipeline uses fake collection, model, search and delivery providers; no live generation or sending occurs.
The isolation regression blocks production snapshot reads and network connections.

SHA-256:
- `listed_cache.json`: `e1f7fa4f253afd40a6d7ad990cea7a81f026c19cbdd8f4c6076d4ed7d62a6d8d`
- `market_cache.json`: `5bbdeed6c68f2f9c9717f43b8c54fd37f8ad738fbedc4058f9382db98377b1b3`
- `posts_latest.json`: `5aabf8c34d475c4b8b1c98c5f455aee316d8ce7c0370d27fb1ffd0bed899147a`
