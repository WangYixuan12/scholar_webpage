# Install
pip install selenium webdriver-manager pypdf

# Usage
# macOS example path (adjust for your username):
```
PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome"
python scholar_webpages_to_singlepage_pdf.py \
  --base-url "https://scholar.google.com/scholar?hl=en&as_sdt=5,33&sciodt=0,33&cites=1359670554775728963&scipsc=" \
  --start-from 0 --start-to 90 --step 10 \
  --user-data-dir "$PROFILE_DIR" \
  --headful \
  --rest-every 8 --cooldown-sec 120 \
  --min-wait 2 --max-wait 7 \
  --out-dir scholar_pages \
  --merged scholar_citations.pdf
```
