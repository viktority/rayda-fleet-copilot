# Assessment dataset

`raw/device-telemetry-dataset.ndjson` is the unmodified file downloaded from the assessment's Google Drive link on 19 November 2026.

- Source: https://drive.google.com/file/d/15HqoteWcUOAEy6aNr0JPjfdM-DMQPd2P/view
- Format: UTF-8 newline-delimited JSON; one device snapshot per line.
- Size: 1,378,932 bytes.
- SHA-256: `fb0ee31ffeeb731b919fc4d5124098c9c5b68cf3fba35eed5092a9f73655c4de`
- Observation window: 14 May to 12 June 2026 (UTC).

Reproduce the profile with Python 3 and its standard library:

```sh
python3 scripts/profile_dataset.py
```

The command writes `reports/dataset-profile.json`. See `reports/dataset-inspection.md` for interpretation and limitations. Source line numbers in the JSON profile refer to this exact raw file; each reference also includes company ID, device ID, and collection timestamp.
