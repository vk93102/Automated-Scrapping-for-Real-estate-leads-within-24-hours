import re
with open('app/page.js', 'r') as f: text = f.read()

correct_header = '''        <header className="top-header">
          <div className="header-left">
            <button className="mobile-menu-btn" onClick={() => setMobileMenuOpen(true)}>
              <Icon path={ICONS.menu} />
            </button>
            <h1 className="page-title">{COUNTIES.find((c) => c.key === activeCounty)?.name} Real Estate Leads</h1>
          </div>
          <div className="user-profile">
            <Icon path={ICONS.database} />
          </div>
        </header>'''

text = re.sub(r'<header className="top-header">.*?</header>', correct_header, text, flags=re.DOTALL)
with open('app/page.js', 'w') as f: f.write(text)
