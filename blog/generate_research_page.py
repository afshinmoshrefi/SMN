#!/usr/bin/env python3
"""
TradeWave Research Page Generator
Generates a static research page from the research template.

Usage:
    python generate_research_page.py
"""

from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
import sys
sys.path.insert(0, '/home/flask')
import config

# =============================================================================
# CONFIGURATION
# =============================================================================

OUTPUT_DIR = config.web_root_dir + "/_static/"
OUTPUT_FILENAME = "research.html"
TEMPLATES_DIR = "/home/flask/blog/templates"

DOMAIN_ROOT = config.domain_root
SIGNUP_URL = "%sregister/?lid=6" % DOMAIN_ROOT
LOGIN_URL = "%smember-login" % DOMAIN_ROOT
LOGOUT_URL = "%smember-logout/?ihcdologout=true" % DOMAIN_ROOT
CONTACT_URL = "%scontact" % DOMAIN_ROOT

DISCLAIMER = (
    "TradeWave is a research platform. It is not a brokerage and "
    "does not execute trades. All data is based on historical analysis "
    "and is provided for informational and educational purposes only. "
    "Past performance does not guarantee future results. Trading and "
    "investing involve substantial risk of loss. You should consult "
    "with a qualified financial advisor before making any investment "
    "decisions. Nothing on this website constitutes a recommendation "
    "to buy or sell any security."
)


def main():
    print("TradeWave Research Page Generator")

    jinja_env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    template = jinja_env.get_template("research.html")

    upgrade_url = "%smy-account/?ihc_ap_menu=subscription" % DOMAIN_ROOT
    wave_viewer_url = "%swave-viewer" % DOMAIN_ROOT

    html = template.render(
        enable_seo=False,
        canonical_url="%s_static/research.html" % DOMAIN_ROOT,
        favicon=config.tw_favicon,
        signup_url=SIGNUP_URL,
        login_url=LOGIN_URL,
        logout_url=LOGOUT_URL,
        upgrade_url=upgrade_url,
        wave_viewer_url=wave_viewer_url,
        contact_url=CONTACT_URL,
        copyright="%d Tara Data Research LLC. All rights reserved." % datetime.now().year,
        disclaimer=DISCLAIMER,
    )

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME
    output_path.write_text(html)

    print("   Generated: %s" % output_path)
    print("   Size: %d bytes" % len(html))
    print("   Done!")


if __name__ == "__main__":
    main()
