#!/usr/bin/python3
# -*- coding: utf-8 -*-

import sys
import os
import logging
import argparse
import base64
import tempfile

from __version__ import __version__
from kivra.auth import KivraAuth
from kivra.api import KivraApiClient
from kivra.receipts import ReceiptFetcher
from kivra.letters import LetterFetcher
from storage.filesystem import FileSystemStoreProvider
from interaction.local import LocalInteractionProvider
from interaction.terminal import TerminalInteractionProvider
from interaction.ntfy import NtfyInteractionProvider
from interaction.web import WebInteractionProvider

def fetch_documents(args, interaction_provider, document_store, temp_dir):
    """
    Fetch documents from Kivra.
    
    Args:
        args: Command line arguments
        interaction_provider: Interaction provider to use
        document_store: Document storage provider to use
        temp_dir: Temporary directory for QR codes and other files
        
    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    stats = {
        "version": 1,
        "status": "in_progress",
        "categories": {"letters": args.fetch_letters, "receipts": args.fetch_receipts},
        "dry_run": args.dry_run,
        "local_verification": isinstance(document_store, FileSystemStoreProvider),
        "limited": (not args.fetch_letters or not args.fetch_receipts
                    or args.max_letters > 0 or args.max_receipts > 0),
        "coverage": {"letters_filter": "inbox",
                     "unverified": ["archived", "uploads", "shared"]},
    }
    try:
        if not document_store.report_run(stats):
            raise OSError("Report storage failure")
        auth = KivraAuth(temp_dir, interaction_provider)
        token_info = auth.authenticate(args.ssn)
        api_client = KivraApiClient(token_info["access_token"], token_info["actor_key"])
        try:
            for enabled, fetcher, method, maximum in (
                (args.fetch_receipts, ReceiptFetcher, "fetch_receipts", args.max_receipts),
                (args.fetch_letters, LetterFetcher, "fetch_letters", args.max_letters),
            ):
                if enabled:
                    stats.update(getattr(fetcher(api_client, document_store), method)(
                        max_count=maximum or None
                    ))
        finally:
            api_client.session.close()
        failed = any(stats.get(f"{kind}_failed", 0)
                     or stats.get(f"{kind}_listing_errors", [])
                     for kind in ("letters", "receipts"))
        stats["status"] = "incomplete" if failed else "complete_selected_scope"
        if not failed and not stats["local_verification"]:
            stats["status"] = "submitted_unverified"
        if args.dry_run:
            stats["status"] = "dry_run"
            for kind in ("letters", "receipts"):
                stats[f"{kind}_stored"] = 0
        exit_code = 1 if failed else 0
    except (Exception, SystemExit):
        logging.error("Export failure. No response data included.")
        stats["status"] = "failed"
        exit_code = 1
    try:
        if not document_store.report_run(stats):
            raise OSError("Report storage failure")
    except Exception:
        logging.error("Export report storage failure.")
        stats["status"] = "failed"
        exit_code = 1
    try:
        interaction_provider.report_completion(stats)
    except Exception:
        logging.error("Export notification failure.")
        return 1
    return exit_code

def main():
    """Main function to fetch receipts and letters from Kivra."""
    
    # Configure logging
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    os.umask(0o077)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description=f'Fetch receipts and letters from Kivra. (version {__version__})',
        prog='kivra-sync'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('ssn', nargs='?', default='personal',
                        help='Output directory label (default: personal). BankID does not use this label.')
    parser.add_argument('--verify-only', action='store_true',
                        help='Do local file verification without authentication')
    
    # Storage provider selection
    parser.add_argument('--storage-provider', choices=['filesystem', 'paperless'], default='filesystem',
                        help='Storage provider to use (default: filesystem)')
    parser.add_argument('--base-dir', help='Base directory for storing documents (default: current directory)')
    
    # Interaction provider selection
    parser.add_argument('--interaction-provider', choices=['terminal', 'web', 'ntfy', 'local'], default='terminal',
                        help='Interaction provider to use (default: terminal; local is deprecated)')
    
    # ntfy provider options
    parser.add_argument('--ntfy-topic', help='ntfy topic to send notifications to')
    parser.add_argument('--ntfy-server', default='https://ntfy.sh', help='ntfy server URL (default: https://ntfy.sh)')
    parser.add_argument('--ntfy-user', help='ntfy username for authentication')
    parser.add_argument('--ntfy-pass', help='ntfy password for authentication')
    parser.add_argument('--trigger-message', default='run now', 
                        help='Message that triggers the script when using a listening interaction provider (default: "run now")')
    
    # Web provider options
    parser.add_argument('--web-port', type=int, default=8080, 
                        help='Port for web interface (default: 8080)')
    parser.add_argument('--web-host', default='0.0.0.0',
                        help='Host for web interface (default: 0.0.0.0)')
    
    # Paperless provider options
    parser.add_argument('--paperless-url', help='Paperless API URL (e.g., http://localhost:8000/api)')
    parser.add_argument('--paperless-token', help='Paperless API token')
    parser.add_argument('--paperless-tags', help='Comma-separated list of tags to apply to all documents')
    parser.add_argument('--dry-run', action='store_true', help='Do not actually store documents, just simulate')
    
    # Fetch options
    parser.add_argument('--fetch-receipts', action='store_true', default=True, help='Fetch receipts')
    parser.add_argument('--no-fetch-receipts', action='store_false', dest='fetch_receipts', help='Do not fetch receipts')
    parser.add_argument('--fetch-letters', action='store_true', default=True, help='Fetch letters')
    parser.add_argument('--no-fetch-letters', action='store_false', dest='fetch_letters', help='Do not fetch letters')
    parser.add_argument('--max-receipts', type=int, default=0, help='Maximum number of receipts to fetch (default: 0, 0 for unlimited)')
    parser.add_argument('--max-letters', type=int, default=0, help='Maximum number of letters to fetch (default: 0, 0 for unlimited)')
    
    args = parser.parse_args()
    if args.max_letters < 0 or args.max_receipts < 0:
        parser.error("Document limits must not be negative")
    if not args.fetch_letters and not args.fetch_receipts:
        parser.error("Select one or more document categories")
    if not args.ssn or args.ssn in ('.', '..') or '/' in args.ssn or '\\' in args.ssn:
        parser.error("Invalid output directory label")
    if args.verify_only and args.storage_provider != 'filesystem':
        parser.error("--verify-only requires filesystem storage")
    
    # Create temp directory for QR codes and other temporary files
    # Prefer env overrides and OS temp; avoid writing into read-only installs
    temp_base = (
        os.environ.get("XDG_RUNTIME_DIR")
        or tempfile.gettempdir()
    )
    temporary = tempfile.TemporaryDirectory(prefix="kivra-sync-", dir=temp_base)
    temp_dir = temporary.name
    
    # Initialize the document storage provider
    if args.storage_provider == 'filesystem':
        # Use base_dir if provided, otherwise use env or current directory
        base_dir = (
            args.base_dir
            if args.base_dir
            else os.getcwd()
        )
        document_store = FileSystemStoreProvider(os.path.join(base_dir, args.ssn), dry_run=args.dry_run)
    elif args.storage_provider == 'paperless':
        # Check if required paperless options are provided
        if not args.paperless_url or not args.paperless_token:
            parser.error("--paperless-url and --paperless-token are required when using the paperless storage provider")
        
        from storage.paperless import PaperlessNgxStoreProvider
        tags = args.paperless_tags.split(',') if args.paperless_tags else None
        document_store = PaperlessNgxStoreProvider(
            api_url=args.paperless_url,
            api_token=args.paperless_token,
            tags=tags,
            dry_run=args.dry_run
        )
    
    if args.verify_only:
        from storage.verify import verify_export
        from interaction.summary import COVERAGE_NOTE
        exit_code = verify_export(document_store)
        print(COVERAGE_NOTE)
        sys.exit(exit_code)

    # Initialize the interaction provider
    if args.interaction_provider == 'local':
        logging.warning("The 'local' interaction provider is deprecated and will be removed in a future release; "
                        "use 'terminal' (default) or 'web'.")
        interaction_provider = LocalInteractionProvider()
    elif args.interaction_provider == 'terminal':
        try:
            interaction_provider = TerminalInteractionProvider()
        except RuntimeError as error:
            parser.error(str(error))
    elif args.interaction_provider == 'ntfy':
        if not args.ntfy_topic:
            parser.error("--ntfy-topic is required when using the ntfy interaction provider")
        
        # Set up authentication headers if provided
        ntfy_headers = {}
        if args.ntfy_user and args.ntfy_pass:
            auth_str = f"{args.ntfy_user}:{args.ntfy_pass}"
            encoded_auth = base64.b64encode(auth_str.encode()).decode()
            ntfy_headers["Authorization"] = f"Basic {encoded_auth}"
        
        interaction_provider = NtfyInteractionProvider(
            topic=args.ntfy_topic,
            server=args.ntfy_server,
            headers=ntfy_headers,
            trigger_message=args.trigger_message
        )
    elif args.interaction_provider == 'web':
        interaction_provider = WebInteractionProvider(
            port=args.web_port,
            host=args.web_host
        )
    
    # Check if the provider can listen
    if interaction_provider.can_listen:
        # Start listening
        print(f"Listening for triggers via {args.interaction_provider}...")
        interaction_provider.listen(lambda: fetch_documents(args, interaction_provider, document_store, temp_dir), temp_dir=temp_dir)
    else:
        # Execute immediately
        exit_code = fetch_documents(args, interaction_provider, document_store, temp_dir)
        sys.exit(exit_code)

if __name__ == "__main__":
    main()
