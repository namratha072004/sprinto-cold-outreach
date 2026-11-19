"""Loads context/sprinto_context.md (who Sprinto is, the case, confirmed decisions). Section 2 'About Sprinto' is the ONLY source of
facts about Sprinto that an email may use. The file's hash is saved on every run so each draft can be traced to this exact version."""
import os, re, hashlib
import settings as S

PATH = os.path.join(S.BASE, "context", "sprinto_context.md")

def load():
    if not os.path.exists(PATH):
        raise RuntimeError("context/sprinto_context.md is missing. A run needs it (it says what may be said about Sprinto).")
    text = open(PATH, encoding="utf-8").read()
    m = re.search(r"## 2\. About Sprinto.*?(?=\n---|\n## 3\.)", text, re.S)
    if not m:
        raise RuntimeError("context/sprinto_context.md has no '## 2. About Sprinto' section.")
    return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(), "sprinto_facts": m.group(0).strip()}

def apply(R):
    ctx = load()
    R.SPRINTO_FACTS = ctx["sprinto_facts"]
    return ctx
