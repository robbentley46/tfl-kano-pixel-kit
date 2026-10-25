"""7x7 weather icons for the weather "now" screen, rows top-to-bottom.

Same string-per-row idea as font.py, but each character names a
colour from COLORS ('.' = off) since the icons are multi-coloured.
Which icon shows for which WMO weather code is decided by
weather.icon_for().
"""

COLORS = {
    'y': [10, 8, 0],   # sun / lightning
    'w': [8, 8, 8],    # moon / snow
    'g': [4, 4, 4],    # cloud
    'b': [0, 2, 10],   # rain
}

ICONS = {
    'sun': [
        "...y...",
        ".y...y.",
        "..yyy..",
        "y.yyy.y",
        "..yyy..",
        ".y...y.",
        "...y...",
    ],
    'moon': [
        "..www..",
        ".ww....",
        "ww.....",
        "ww.....",
        "ww.....",
        ".ww....",
        "..www..",
    ],
    'part_cloud': [
        "...y..y",
        "....yy.",
        "..ggyyy",
        ".gggggy",
        "ggggggg",
        "ggggggg",
        ".......",
    ],
    'cloud': [
        ".......",
        "..ggg..",
        ".ggggg.",
        "ggggggg",
        "ggggggg",
        ".......",
        ".......",
    ],
    'rain': [
        "..ggg..",
        ".ggggg.",
        "ggggggg",
        ".......",
        ".b.b.b.",
        ".......",
        "b.b.b..",
    ],
    'snow': [
        "..ggg..",
        ".ggggg.",
        "ggggggg",
        ".......",
        ".w.w.w.",
        ".......",
        "w.w.w..",
    ],
    'thunder': [
        "..ggg..",
        ".ggggg.",
        "ggggggg",
        "...yy..",
        "..yy...",
        "...y...",
        "..y....",
    ],
}
