"""Community: opt-in sharing of a signed station card between Zenivo stations by gossip over HTTP(S).

No central server: every station serves its known cards at /api/community/peers and exchanges them with a few
other stations every 15 minutes. Nothing runs, and no key is created, while the feature is disabled."""

# Well-known stations asked in addition to the owner's own seed list (never contacted while Community is off)
DEFAULT_SEEDS = ["https://sky.hoan.uk", "https://allsky.hoan.uk"]

CARD_VERSION = 1
