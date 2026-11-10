# Kivra sync

An automation tool that connects to Kivra (a Swedish digital mailbox service) to download and organize your digital receipts and letters, helping you maintain a local backup of your important documents.

This tool is originaly based on a script created here https://github.com/stefangorling/fetch-kivra, thanks Stefan! It has since gone through major changes and feature additions.

## Disclaimer

This project is not affiliated with Kivra in any way. It is an independent, open-source tool created by the community to help users download their documents from Kivra - a feature that is otherwise only available through manual clicks in their user interface.

We believe individuals should have easy access to their own data. Digital mailboxes like Kivra hold important documents on behalf of users, and the lack of an efficient export option makes it unnecessarily cumbersome for users to retrieve and back up their documents. This tool aims to bridge that gap.

Use it responsibly and at your own discretion.

## Key Features

- **Authentication**: Fetches and displays a QR code for authentication via BankID.
- **Flexible Storage**: Store documents locally on your filesystem or integrate with Paperless-ngx for advanced document management
- **Multiple Interaction Modes**: Scan the BankID QR code right in your terminal, or set up a "headless" mode that listens for triggers and shows QR codes via a web page or ntfy.sh

## Quick Start

This will:
1. Authenticate with Kivra using BankID
2. Fetch receipts and letters
3. Store them in the filesystem

### Option 1: Using Docker (recommended)

Build the image:

```bash
docker build -t kivra-sync:local .
```

Start the export in a terminal:

```bash
scripts/export-local.sh
```

Scan the QR code with BankID. Follow the BankID instructions for the Kivra login.
The export uses one authentication session for receipts and letters.
It does not change or remove remote Kivra documents.

To keep source files without HTML-to-PDF conversion:

```bash
scripts/export-local.sh --no-convert
```

This filesystem option keeps PDFs, HTML, plain text, and JSON metadata from Kivra.
It creates no PDFs from HTML and does not remove PDFs from earlier exports.
HTML can contain links to external resources. The exporter does not download those resources.
The default export also converts HTML to PDF.

The QR login does not use an identity number from the command line.
The default output directory is `~/kivra-export/personal/`.
An optional positional argument selects a different directory label.
For an older export, use its initial label and base directory.

Set `KIVRA_EXPORT_DIR` to select a different base directory.
The script selects the correct user mapping for rootless Docker.
It disables container logs and writes only to the export directory and temporary memory.

Do not start two exports to the same directory.
Keep edited copies of documents in a different directory.
A new export replaces files that fail local verification.

After the export stops, do local verification without a network connection:

```bash
scripts/export-local.sh --verify-only
```

The check uses the last export report and SHA-256 manifests.
A manifest records the size and hash of each file in a document.
The check reports missing or changed files.
It does not prove that Kivra returned all document categories.

For download failures or missing files, start the export again with the same directory.
The program downloads files without manifests again.
An existing JSON file does not prove document completeness.

- `-it` is required, since the QR code is shown in the terminal.
- `--base-dir /data` makes the documents land in the mounted volume rather than inside the container.
- Before login, the terminal must be at least 60 columns × 32 rows. A typical compact QR and its status line need about 54 columns × 29 rows; larger payloads may require more space, which the error message will report.

### Option 2: Using Docker Compose (headless, web UI)

For a long-running setup without an interactive terminal, use the `web` interaction provider and open the QR code in your browser:

```bash
docker compose build && docker compose up
```

Open `http://127.0.0.1:8080` to start an export and scan the QR code.
The supplied configuration uses a loopback port and disables Docker logs.
Keep this unauthenticated interface on loopback.

The supplied Compose setup has neither an interactive terminal for `terminal` nor a desktop image viewer for `local`, so it uses `web`. The `ntfy` provider is another option for headless runs.

### Option 3: Using Python

```bash
# Install system dependencies (Debian/Ubuntu)
apt-get install weasyprint

# Install Python dependencies
pip install -r requirements.txt

# Run the script
python kivra_sync.py YYYYMMDDXXXX
```

### Option 4: Using Nix (flake)

If you have Nix with flakes enabled:

```bash
# Run (uses the flake-provided environment)
nix run . -- --help

# Typical run, storing under ./data/<ssn>/
nix run . -- YYYYMMDDXXXX --base-dir ./data

# Run with the web interaction provider
nix run . -- YYYYMMDDXXXX --interaction-provider web --web-port 8080

# Build and run help on resulting executable
nix build && result/bin/kivra-sync --help 

# Dev shell with Python + deps + system libs
nix develop

```

## Export checks and limits

The filesystem provider writes temporary files, flushes them, and uses atomic replacement.
It writes a completion manifest only after metadata and all supported parts succeed.
A new export compares each file with that manifest before it skips a document.
This includes the HTML source and, unless `--no-convert` is set, its PDF conversion.
The offline check uses the conversion setting from the last export report.

The private `export-report.json` records selected items, results, and error reasons.
Keep this report and all exported data out of Git and public issue reports.
With filesystem storage, console output contains counts and item positions, not document keys, names, or response bodies.
Item positions refer to the saved listing for that category.

Exit code `0` means that all selected items passed the requested operation.
Exit code `1` indicates a failure in a download, listing, storage operation, or local check.
Invalid command arguments return `2`.
The status `complete_selected_scope` does not mean a complete account export.
The report shows explicit limits and disabled categories.
Dry runs store no files and report `dry_run`, not export success.

The PDF checks examine the header and end marker.
If an uncompressed response specifies its length, the actual length must agree.
These checks do not prove that each PDF page has correct content.
SHA-256 checks find local changes after storage, not subsequent changes on Kivra.

Letter parts support PDF, plain text, and HTML.
Unknown content types, missing parts, and attachment download errors cause a failure result.
The export keeps supported parts when another part fails.
HTML conversion cannot read local files or request external resources.
If conversion fails, the HTML stays in the export, but the document status is `failed`.

With `--no-convert`, a complete document requires its source files, not converted PDFs.
This option does not suppress errors in document or attachment downloads.
An export with conversion enabled cannot skip HTML that has no verified PDF conversion.

Atomic writes, manifests, and offline checks apply to filesystem storage.
Paperless retains its existing document lookup.
Its result is `submitted_unverified`, not a verified local export.

### Category coverage

The current query in [kivra/models.py](kivra/models.py) uses `ContentListFilter`.
The caller in [kivra/letters.py](kivra/letters.py) supplies only `inbox`.
The earlier [fetch-kivra source](https://github.com/stefangorling/fetch-kivra/blob/main/fetch-kivra.py)
also uses `inbox`. These sources do not specify an archive filter or its meaning.

The listing includes `isUpload` and `accessInfo.owner.isMe`.
Those fields give data about returned items. They do not prove that the listing includes all uploads or shared items.
Coverage of archived documents, uploads, and shared items is unverified.
Receipt attachments use `GET /v1/attachments/{id}` with `Accept: application/pdf`.
The [download handler](https://inbox.kivra.com/dispatch-DFjLvkbw-1.319.1.js)
and [HTTP client](https://inbox.kivra.com/typography-DrF9Dkl4-1.319.1.js)
in Kivra web client `1.319.1` specify this request and its authentication.

The exporter saves each attachment as a PDF in the receipt directory, with a `_partN` suffix.
The private metadata records attachment IDs in sequence.
The completion manifest requires the receipt PDF and all its attachment PDFs.

An attachment error does not stop subsequent downloads. The exporter keeps downloaded files.
A new export attempts receipts with missing or changed attachment files again.
`--no-convert` also downloads attachment PDFs. Kivra provides these PDFs directly.

Before calling an export complete, compare category counts with the Kivra interface.
Do not infer coverage from exit code `0`.
More category support needs documented API behavior or a private, read-only check of actual behavior.
Do not post live API responses, identity numbers, or credentials to an issue.

### Offline regression tests with Docker

Build the image before these tests:

```bash
docker run --rm --network none --entrypoint sh \
  --read-only --tmpfs /tmp \
  -v "${PWD}:/tests:ro" -w /tests kivra-sync:local \
  -c 'python -m unittest discover -v && python test_qr_refresh.py'
```

Tests use synthetic data and do not contact Kivra.

## Advanced Configuration

### Storage Providers

#### Filesystem Storage (Default)

Documents are stored in the local filesystem. 

```bash
python kivra_sync.py YYYYMMDDXXXX --storage-provider filesystem --base-dir /path/to/store
```

#### Paperless-ngx Storage

Upload documents directly to Paperless-ngx:

```bash
python kivra_sync.py YYYYMMDDXXXX --storage-provider paperless \
  --paperless-url http://your-paperless-server:8000/api \
  --paperless-token your_paperless_api_token \
  --paperless-tags "kivra,receipts,automated"
```

### Interaction Providers

Interaction providers handle user interaction during authentication and report completion statistics.

#### Terminal Interaction (Default)

Draws the BankID QR code directly in the terminal and refreshes it on every poll, so the code you scan is always current. Reports progress to the console:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider terminal
```

Uses Unicode half-block characters to fit two QR rows into each terminal line. Requires an interactive terminal (use `docker run -it` in Docker) with a font that supports these characters. Startup checks for at least 60 columns × 32 rows before authentication; the exact size is checked again when the QR payload arrives. A typical compact QR and its status line need about 54 columns × 29 rows, but larger payloads may require more. Windows console ANSI handling is initialized automatically. If output isn't a terminal, use the `web` or `ntfy` provider instead.

When upgrading, cron, systemd, and Docker runs without `-it` must select `web` or `ntfy` explicitly because `terminal` is now the default. If the window is resized too small during authentication, QR updates pause until it is enlarged again.

#### Web Interaction

Provides a web interface for triggering syncs and viewing results:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider web --web-port 8080
```

Then navigate to `http://localhost:8080` in your browser to access the interface. Perfect for containerized deployments where GUI access is not available.

#### ntfy Interaction

Sends QR codes and reports via ntfy.sh, with optional listening mode:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider ntfy --ntfy-topic your-topic
```

To trigger the script in listening mode, send the trigger message to the ntfy topic:

```bash
curl -d "run now" ntfy.sh/your-topic
```

#### Local Interaction (deprecated)

Opens the QR code image in your system's image viewer. It only shows a single static frame, which BankID may reject as stale ("QR-koden är ogiltig" / "QR code is invalid", RFA17). This provider will be removed in a future release; use `terminal` instead.

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider local
```

### Fetch Options

Customize which documents to fetch and how many:

```bash
# Fetch only letters
python kivra_sync.py YYYYMMDDXXXX --no-fetch-receipts

# Fetch only receipts
python kivra_sync.py YYYYMMDDXXXX --no-fetch-letters

# Fetch up to 10 letters and 5 receipts
python kivra_sync.py YYYYMMDDXXXX --max-letters 10 --max-receipts 5

# Fetch unlimited receipts
python kivra_sync.py YYYYMMDDXXXX --max-receipts 0
```

## Command-Line Reference

### General Options
| Option | Description |
|--------|-------------|
| `LABEL` (optional positional) | Output directory label (default: `personal`). No identity number required. |
| `--verify-only` | Local verification of the last export without authentication. |
| `--dry-run` | Do not actually store documents, just simulate |

### Storage Options
| Option | Description |
|--------|-------------|
| `--storage-provider {filesystem,paperless}` | Storage provider to use (default: filesystem) |
| `--base-dir DIR` | Base directory for storing documents (default: current working directory) |
| `--no-convert` | Keep source files without HTML-to-PDF conversion (filesystem only). |

### Paperless-specific Options
| Option | Description |
|--------|-------------|
| `--paperless-url URL` | Paperless API URL (required for paperless) |
| `--paperless-token TOKEN` | Paperless API token (required for paperless) |
| `--paperless-tags TAGS` | Comma-separated list of tags to apply to all documents |

### Interaction Options
| Option | Description |
|--------|-------------|
| `--interaction-provider {terminal,web,ntfy,local}` | Interaction provider to use (default: terminal; `local` is deprecated) |
| `--ntfy-topic TOPIC` | ntfy topic to send notifications to (required for ntfy) |
| `--ntfy-server URL` | ntfy server URL (default: https://ntfy.sh) |
| `--ntfy-user USER` | ntfy username for authentication |
| `--ntfy-pass PASS` | ntfy password for authentication |
| `--web-port PORT` | Port for web interface (default: 8080) |
| `--web-host HOST` | Host for web interface (default: 0.0.0.0) |
| `--trigger-message MSG` | Message that triggers the script (default: "run now") |

### Document Fetch Options
| Option | Description |
|--------|-------------|
| `--fetch-receipts` / `--no-fetch-receipts` | Enable/disable receipt fetching (default: enabled) |
| `--fetch-letters` / `--no-fetch-letters` | Enable/disable letter fetching (default: enabled) |
| `--max-receipts N` | Maximum number of receipts to fetch (0 for unlimited) |
| `--max-letters N` | Maximum number of letters to fetch (0 for unlimited) |

## Project Structure

The code is organized into logical modules:
- `kivra/`: Kivra-specific functionality (auth, API, models)
- `storage/`: Document storage providers
- `interaction/`: User interaction providers
- `utils/`: Utility functions
