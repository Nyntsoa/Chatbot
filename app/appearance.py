from pathlib import Path

THEMES = {
    "light": (
        "Clair",
        "light",
        "#f8fafc",
        "#ffffff",
        "#edf1f5",
        "#17212e",
        "#49586a",
        "#8e9bab",
        "#1d5fbf",
        "#ffffff",
    ),
    "dark": (
        "Sombre",
        "dark",
        "#121417",
        "#1c2025",
        "#242b33",
        "#e4ecf6",
        "#aebccc",
        "#627286",
        "#8abcff",
        "#111d2a",
    ),
    "forest": (
        "Forêt",
        "dark",
        "#101c18",
        "#172923",
        "#1e352d",
        "#e4f1e9",
        "#acc8b8",
        "#648779",
        "#87dcba",
        "#10261d",
    ),
    "contrast": (
        "Contraste élevé",
        "light",
        "#ffffff",
        "#ffffff",
        "#eef3f8",
        "#0b111c",
        "#26354a",
        "#455769",
        "#163f8a",
        "#ffffff",
    ),
}
SYSTEM_THEME = "system"
THEME_LABELS = {
    SYSTEM_THEME: "Suivre l’appareil",
    **{key: palette[0] for key, palette in THEMES.items()},
}
STYLE_PATH = Path(__file__).with_name("themes.css")
TOKENS = (
    "scheme",
    "ground",
    "surface",
    "field",
    "ink",
    "muted",
    "border",
    "accent",
    "on-accent",
)


def theme_variables(theme):
    values = THEMES[theme][1:]
    return (
        ":root {"
        + "".join(
            f"--gb-{token}:{value};"
            for token, value in zip(TOKENS, values, strict=True)
        )
        + "}"
    )


def theme_markup(theme):
    if theme == SYSTEM_THEME:
        variables = (
            theme_variables("light")
            + "@media(prefers-color-scheme: dark){"
            + theme_variables("dark")
            + "}"
        )
    else:
        variables = theme_variables(theme)
    return f"<style>{variables}\n{STYLE_PATH.read_text(encoding='utf-8')}</style>"
