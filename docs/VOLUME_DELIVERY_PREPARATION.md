# Optional Volume Delivery Preparation

This branch is a local-only paired candidate over active SMN 94023f7. The engine
candidate is `codex/volume-delivery-exporter-20261013` in tradewave-tw2. Neither
candidate is activated. Existing omission fallback, publication state, scheduler,
newsletter journal and normal model allowance remain untouched.

V1 observed-timestamp sidecars remain compatible. V2 date-only engine exports
retain each row's source CSV hash and actual session date, a separate generated
clock and the engine's successful completion proof. Date-only observations never
acquire a fabricated observation timestamp or a current-RVOL ranking bonus.
Maximum possible age is bounded from the source date's New York midnight; it does
not independently establish the latest market session. Export time cannot make
old observations current. Original supplied financial values are unchanged.

The receiver requires the configured `config.appserver_ip` and an exact non-secret
`SMN_VOLUME_PRODUCER_COMMIT` pin. Direct foreign socket peers cannot authorize
themselves using X-Real-IP. Before activation, prove the existing loopback nginx
listener overwrites that header from its real socket peer; otherwise this trust
path must not be enabled. No new credentials or access grants are introduced.
Generation files remain immutable and receipt/hash bound; the selector reads the
new pointer only when present. Invalid/incomplete inputs still produce omission,
never an article-publication hold. Legacy uploads cannot rewrite immutable
generation contents.

Preparation tests cover source/value fidelity, date/hash/session mismatch,
interrupted commit/acknowledgement, repeat identity and source-peer/revision
validation. A passing local suite is not independent acceptance or an authorized
production release. Pair packaging, final failure tests, native qualification and
bounded target-specific release/rollback plan remain pending at this checkpoint.
