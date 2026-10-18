#!/usr/bin/env python3
"""
make_demo_tree.py — build a fake media estate to try media-index on.

Creates three "drives" under ROOT (media1, media2, archive) holding a few
thousand SPARSE files: films, TV with gaps, albums, photos, ebooks, a backup
with duplicates, and a code tree with .git/node_modules. Files report realistic
sizes (terabytes in total) but occupy almost no disk space.

Usage:  ./demo/make_demo_tree.py ROOT
"""
import os
import random
import sys

R = random.Random(42)
GB, MB = 10**9, 10**6

FILMS = ["Nosferatu (1922)", "The General (1926)", "Metropolis (1927)", "Night of the Living Dead (1968)",
         "His Girl Friday (1940)", "Charade (1963)", "The Kid (1921)", "Safety Last! (1923)", "Sherlock Jr. (1924)",
         "The Phantom of the Opera (1925)", "Battleship Potemkin (1925)", "The Gold Rush (1925)",
         "Plan 9 from Outer Space (1957)", "Carnival of Souls (1962)", "The Little Shop of Horrors (1960)",
         "Detour (1945)", "D.O.A. (1949)", "The Last Man on Earth (1964)", "House on Haunted Hill (1959)",
         "Things to Come (1936)", "The 39 Steps (1935)", "The Lady Vanishes (1938)", "Meet John Doe (1941)",
         "My Man Godfrey (1936)", "The Stranger (1946)", "Scarlet Street (1945)", "The Hitch-Hiker (1953)",
         "Beat the Devil (1953)", "A Star Is Born (1937)", "Nothing Sacred (1937)", "Dementia 13 (1963)",
         "The Terror (1963)", "Robot Monster (1953)", "The Killer Shrews (1959)", "Terror by Night (1946)"]
TAGS = ["1080p.BluRay.x264", "720p.BluRay", "1080p.WEB-DL", "DVDRip.XviD", "2160p.Remux"]
SHOWS = {"Dragnet": (1, 3, 12), "Bonanza": (2, 4, 16), "The Lone Ranger": (1, 2, 20),
         "One Step Beyond": (1, 3, 10), "The Cisco Kid": (3, 5, 13)}
ALBUMS = {("Johann Sebastian Bach", "Goldberg Variations (1981)"): 32, ("Ludwig van Beethoven", "Symphony No. 5 (1963)"): 4,
          ("Antonio Vivaldi", "The Four Seasons (1984)"): 12, ("Gustav Holst", "The Planets (1961)"): 7,
          ("Erik Satie", "Gymnopedies (1979)"): 3, ("Frederic Chopin", "Nocturnes (1965)"): 21,
          ("Claude Debussy", "Preludes Book 1 (1978)"): 12, ("Wolfgang Amadeus Mozart", "Requiem (1971)"): 14,
          ("Edvard Grieg", "Peer Gynt Suites (1960)"): 8, ("Modest Mussorgsky", "Pictures at an Exhibition (1958)"): 16}
BOOKS = {"Jane Austen": ["Pride and Prejudice", "Emma", "Persuasion", "Sense and Sensibility"],
         "Charles Dickens": ["Great Expectations", "Bleak House", "Little Dorrit", "Hard Times", "Oliver Twist"],
         "Mark Twain": ["Roughing It", "Life on the Mississippi", "The Prince and the Pauper"],
         "Arthur Conan Doyle": ["The Sign of the Four", "A Study in Scarlet", "The Lost World"],
         "H. G. Wells": ["The Time Machine", "The War of the Worlds", "The Invisible Man", "Kipps"],
         "Jules Verne": ["Around the World in Eighty Days", "Journey to the Centre of the Earth"],
         "Mary Shelley": ["Frankenstein", "The Last Man"], "Bram Stoker": ["Dracula", "The Jewel of Seven Stars"],
         "Herman Melville": ["Moby-Dick", "Typee", "Bartleby, the Scrivener"],
         "Joseph Conrad": ["Heart of Darkness", "Lord Jim", "Nostromo", "The Secret Agent"],
         "Robert Louis Stevenson": ["Treasure Island", "Kidnapped", "The Black Arrow"]}


def touch(path, size, year=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return
    with open(path, "wb") as f:
        f.truncate(int(size))  # sparse: reports the size, stores nothing
    t = R.randint(1325376000, 1735689600) if year is None else R.randint(0, 31_000_000) + (year - 1970) * 31_557_600
    os.utime(path, (t, t))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = os.path.abspath(sys.argv[1])
    m1, m2, ar = (os.path.join(root, d) for d in ("media1", "media2", "archive"))
    films = {}
    for title in FILMS:
        tag = R.choice(TAGS)
        size = {"DVDRip.XviD": R.uniform(0.6, 0.8), "2160p.Remux": R.uniform(45, 70)}.get(tag, R.uniform(1.5, 9)) * GB
        name = f"{title.replace(' ', '.')}.{tag}.mkv"
        films[title] = (name, size)
        touch(f"{m1}/Films/{title}/{name}", size)
        touch(f"{m1}/Films/{title}/{title}.en.srt", R.uniform(40, 120) * 1000)
    for title in R.sample(FILMS, 12):  # the backup drive holds exact copies of some
        name, size = films[title]
        touch(f"{m2}/Backup/Films/{title}/{name}", size)
        touch(f"{m2}/Backup/Films/{title}/{title}.en.srt", R.uniform(40, 120) * 1000)
    for show, (first, last, eps) in SHOWS.items():
        for s in range(first, last + 1):
            for e in range(1, eps + 1):
                if R.random() < 0.06:
                    continue  # a missing episode, for the gap report
                touch(f"{m1}/TV/{show}/Season {s:02d}/{show} - S{s:02d}E{e:02d}.mkv", R.uniform(0.35, 1.6) * GB)
    for (artist, album), n in ALBUMS.items():
        for t in range(1, n + 1):
            touch(f"{m1}/Music/{artist}/{album}/{t:02d} - Track {t}.flac", R.uniform(18, 60) * MB)
        touch(f"{m1}/Music/{artist}/{album}/cover.jpg", R.uniform(0.2, 1.5) * MB)
        if R.random() < 0.5:
            for t in range(1, n + 1):
                touch(f"{m2}/Music (mp3)/{artist}/{album}/{t:02d} - Track {t}.mp3", R.uniform(5, 12) * MB)
    for i in range(1, 1601):
        year = 2014 + i % 11
        touch(f"{m2}/Photos/{year}/IMG_{i:04d}.JPG", R.uniform(2, 9) * MB, year)
    for vm in ("windows-11", "ubuntu-24.04", "build-box"):
        touch(f"{m2}/VMs/{vm}/{vm}.vmdk", R.uniform(120, 400) * GB)
    for pile in ("isos", "installers", "papers", "misc"):
        for i in range(R.randint(10, 25)):
            ext = {"isos": "iso", "installers": "zip", "papers": "pdf", "misc": R.choice(["rar", "7z", "txt"])}[pile]
            size = {"iso": R.uniform(1, 8) * GB, "zip": R.uniform(20, 900) * MB, "pdf": R.uniform(0.2, 9) * MB}.get(ext, R.uniform(1, 400) * MB)
            touch(f"{m1}/Downloads/{pile}/{pile}-{i:03d}.{ext}", size)
    n = 1000
    for author, titles in BOOKS.items():
        for title in titles:
            n += R.randint(3, 400)
            d = f"{ar}/Ebooks/{author}/{title} ({n})"
            touch(f"{d}/{title} - {author}.epub", R.uniform(0.3, 4) * MB)
            touch(f"{d}/cover.jpg", R.uniform(50, 300) * 1000)
            touch(f"{d}/metadata.opf", R.uniform(2, 6) * 1000)
            touch(f"{ar}/Ebooks backup/{author}/{title} ({n})/{title} - {author}.epub", R.uniform(0.3, 4) * MB)
    for course, n in (("Introduction to Astronomy", 24), ("History of the Roman Republic", 36)):
        for i in range(1, n + 1):
            touch(f"{ar}/Lectures/{course}/Lecture {i:02d}.mp4", R.uniform(0.3, 0.9) * GB)
    for i in range(400):
        touch(f"{ar}/Code/website/node_modules/pkg{i % 40}/lib/file{i}.js", R.uniform(1, 80) * 1000)
    for i in range(120):
        touch(f"{ar}/Code/website/.git/objects/{i:02x}/{R.getrandbits(64):016x}", R.uniform(0.2, 40) * 1000)
    touch(f"{ar}/Code/website/index.html", 4200)
    print(f"demo tree written under {root}")


if __name__ == "__main__":
    main()
