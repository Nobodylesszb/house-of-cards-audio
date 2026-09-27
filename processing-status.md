# Processing status

Status: text curation and validation complete for 72/73 episodes; S06E07 Deferred; no audio generated; source files unchanged.

## Processing progress

| Season | Text-complete episodes | Products |
|---|---:|---|
| S01 | 13/13 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md` |
| S02 | 13/13 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md` |
| S03 | 13/13 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md` |
| S04 | 13/13 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md` |
| S05 | 13/13 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md` |
| S06 | 7/8 | `canonical.tsv`, `listening.tsv`, `extended.tsv`, `skipped.tsv`, `index.md`; S06E07 Deferred |

## Inventory snapshot

| Season | MP3 coverage | Audio filename family | Subtitle candidates | Preferred English source | Main alignment risk |
|---|---:|---|---|---|---|
| S01 | 13/13 | `1-6季MP3/第1季MP3/*S01E##*.HR-BluRay.AC3.mp3` | 26 root `.ass` (2 bilingual variants per episode) | `字幕文件/第一季字幕/*S01E##*.en&chs.ass` or `en&cht.ass`; filter the English `Default1` events | MP3 is HR BluRay/AC3 while subtitles are BluRay X264/AAC; duration/end-credit differences; strip any metadata events |
| S02 | 13/13 | `1-6季MP3/第2季MP3/*S02E##*.WEBrip*.mp3` | 26 root `.ass` (2 bilingual variants per episode) | `字幕文件/第二季字幕/*S02E##*.BluRay.en&chs.ass` or `en&cht.ass`; filter English `Default1` events | BluRay subtitles paired with WEBrip MP3; opening/recap and timing version may differ; S02 files contain subtitle-team credits |
| S03 | 13/13 | mixed `HDTVrip.1024X512` and `HD720P...English.CHS-ENG` families | E01–E06,E08–E13: 36 `.rar` packs (3 release candidates/episode); E07: 3 extracted packs, each 4 `.ass` + 5 `.srt` | After extraction, use `...NF.WEBRip...-NTb...英文.srt`; E07 is already extracted | Archives are not directly usable; audio mixes HDTV/WEB releases; NTb, DDLV, and 2HD timing may differ; verify anchors |
| S04 | 13/13 | `1-6季MP3/第4季MP3/*S04E##*.WEBrip.1024X512.mp3` | 13 root NF `2160p...iON.srt` + 13 nested DEFLATE packs (4 `.ass` + 5 `.srt` each) | Nested `字幕文件/第四季字幕/House.of.Cards.2013.S04E##...DEFLATE...英文.srt`; root NF SRT is bilingual cross-check | MP3 is 1024x512 WEB while candidates are 720p/2160p WEB; nested English files contain trailing `www.ZiMuZu.tv` metadata cues; strip before boundary checks |
| S05 | 13/13 | `1-6季MP3/第5季MP3/*S05E##*.BD-HR...mp3` | 13 nested demand Bluray packs (normally 4 `.ass` + 5 `.srt` each) | `字幕文件/第五季字幕/...s05e##...demand...英文.srt` | Source family is the closest match (BD/Bluray), but version/end-credit deltas remain; E08 has duplicate `简体(1).ass`, ignore as duplicate |
| S06 | 8/8 | `1-6季MP3/第6季MP3/*S06E##*.WEB.720P-人人影视.mp3` | E01–E06 and E08 have English-track candidates; E07 has Chinese-only subtitles | E01–E05: `...NTG.chs.eng...英文.srt`; E06: main UTF-16 bilingual `.ass`; E08: nested `[zmk.pw]...Chapter.73...英文.srt` | S06 has no Frank aside layer; E07 lacks English/bilingual timing; E08 has alternate Chapter.70/Chapter.73 packs; repeated/misfiled IDs require manual verification |

## Episode coverage

All rows below are verified by filename scan; `E01–E13` means every episode in that range is present.

| Season | Episodes | Count | MP3 notes | Subtitle status |
|---|---|---:|---|---|
| S01 | E01–E13 | 13 | One common HR-BluRay/AC3 family | 2 bilingual ASS per episode; English is embedded in `Default1` events |
| S02 | E01–E13 | 13 | One common WEBrip/AAC family | 2 bilingual ASS per episode; English is embedded in `Default1` events; opening credits present |
| S03 | E01–E13 | 13 | E01,E03,E04,E10–E13 HDTVrip; E02,E05–E09 HD720P | E01–E06,E08–E13 have three RAR packs each; E07 has three extracted release packs |
| S04 | E01–E13 | 13 | One common WEB 1024x512 family; E13 filename includes `End` | Direct bilingual NF SRT plus nested English-only SRT per episode |
| S05 | E01–E13 | 13 | One common BD-HR/720P family; E13 filename includes `End` | English-only SRT per episode; bilingual ASS/SRT also present |
| S06 | E01–E08 | 8 | One common WEB 720P family; E08 filename includes `End` | E01–E05 clean English SRT; E06 usable UTF-16 bilingual ASS; E07 Chinese-only; E08 clean English SRT only in Chapter.73 pack |

## Candidate details and known exceptions

- S01/S02 `.ass` files are bilingual tracks, not English-only files. The English events are present under the second style (`Default1`); do not mistake Chinese-only early events for missing English.
- S03 RAR packs contain the same useful shape (English-only SRT plus bilingual ASS/SRT). Only S03E07 is extracted in place; do not treat an unextracted archive as a ready subtitle file.
- S04 nested English-only SRTs include subtitle-site URL cues after the actual dialogue in at least the inspected pack; remove such cues before deriving segment boundaries. The root `2160p NF` SRT is a bilingual timing cross-check.
- S05 E08 contains a duplicate `简体(1).ass`; no extra English candidate.
- S06E01's folder contains a stray `S06E02...ass`; S06E08's `Chapter.70` folder contains a stray `S06E06...ass`. Ignore both. S06E08's `[zmk.pw]...Chapter.73` nested package is the complete English candidate.
- S06E06 uses the main UTF-16 bilingual ASS, whose English `Default` and Chinese `Default2` tracks require overlap-based alignment. S06E07 remains Chinese-only; do not manufacture English text from it.
- Subtitle-team notices (`*.txt`, URLs, translator/time-axis credits) are metadata and excluded from all tiers.

## Deferred episodes

- **S06E07 — Deferred:** only pure-Chinese subtitles are currently available. A complete English or bilingual timeline is required; episode audio exists, but ASR is outside this processing pass. No placeholder five-file directory is created.

## Alignment gate

Before accepting any item, compare the chosen subtitle and MP3 at start, middle, and end. Keep only a complete, contiguous speech boundary. If an aside is interrupted, preserve separate continuous parts and label them `frank-aside part-N`; do not join across dialogue. Record unresolved or rejected Frank candidates in `skipped.tsv` with a short reason once segment review begins.

## Read-only verification notes

- Filename scan: 73/73 MP3s present for S01–S06; 13 episodes each in S01–S05 and 8 in S06.
- No global subtitle shift is assumed. After ignoring obvious metadata cues, observed MP3-duration minus subtitle-final-dialogue differences are approximately S01 `98–223s`, S02 `138–239s`, S03E07 `235–243s`, S04 `158–257s`, S05 `136–258s`, and S06 clean candidates `168–466s`. These are version/end-credit warnings, not correction values.
