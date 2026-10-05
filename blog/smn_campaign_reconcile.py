"""Operator-installed production GET-only campaign status tick."""
import json

def main():
    import install_smn_primary_edition as installer
    installer.configure_production()
    installer.guard()
    from send_smn_emails import poll_pending_campaigns
    print(json.dumps(poll_pending_campaigns()))
    return 0

if __name__=='__main__':raise SystemExit(main())
