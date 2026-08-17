"""
Earnings — what the marketplaces say they owe and have paid.

Read-only. Nothing in here is produced by this app; every row is copied from
a page on someone else's site, so nothing in here is authoritative about our
own pipeline. It answers the one question the pipeline cannot: is any of this
making money.

  faa.py       reading FineArtAmerica (no browser — the pages are HTML)
  matching.py  attributing a sale to one of our designs
  service.py   storing rows, running the nightly read, the figures a screen needs

Adding a marketplace means a new reader module with the same two jobs — log
in, and return rows — plus an entry in the dispatch table in service.py.
Nothing else should need to know the marketplace exists.
"""
