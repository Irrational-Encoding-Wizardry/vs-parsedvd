from __future__ import annotations

import vapoursynth as vs
from io import BufferedReader
from abc import abstractmethod
from fractions import Fraction
from pyparsedvd import vts_ifo
from functools import lru_cache
from itertools import accumulate
from typing import List, Tuple, cast, Any, Dict, Sequence

from .utils.types import Range
from .utils.spathlib import SPath
from .DVDIndexers import D2VWitch, DGIndexNV, DGIndex
from .dataclasses import D2VIndexFileInfo, DGIndexFileInfo, IFOFileInfo, IndexFileType

core = vs.core


class IsoFileCore:
    _subfolder = "VIDEO_TS"
    _idx_path: SPath | None = None
    _mount_path: SPath | None = None
    _clip: vs.VideoNode | None = None
    index_info: Sequence[IndexFileType | None] = [None] * 64
    split_clips: List[vs.VideoNode] | None = None
    joined_clip: vs.VideoNode | None = None
    split_chapters: List[List[int]] | None = None
    joined_chapters: List[int] | None = None

    def __init__(
        self, path: SPath, indexer: D2VWitch | DGIndexNV | DGIndex = D2VWitch(),
        safe_indices: bool = False, force_root: bool = False
    ):
        self.iso_path = SPath(path).absolute()
        if not self.iso_path.is_dir() and not self.iso_path.is_file():
            raise ValueError(
                "IsoFile: path needs to point to a .ISO or a dir root of DVD"
            )

        self.indexer = indexer
        self.safe_indices = safe_indices
        self.force_root = force_root

    def source(self, **indexer_kwargs: Dict[str, Any]) -> vs.VideoNode:
        if self._mount_path is None:
            self._mount_path = self._get_mount_path()

        vob_files = [
            f for f in sorted(self._mount_path.glob('*.[vV][oO][bB]')) if f.stem != 'VIDEO_TS'
        ]

        if not len(vob_files):
            raise FileNotFoundError('IsoFile: No VOBs found!')

        self._idx_path = self.indexer.get_idx_file_path(self.iso_path)

        self.index_files(vob_files)

        ifo_info = self.get_ifo_info(self._mount_path)

        self._clip = self.indexer.vps_indexer(
            self._idx_path, **self.indexer.indexer_kwargs, **indexer_kwargs
        )

        self._clip = self._clip.std.AssumeFPS(
            fpsnum=ifo_info.fps.numerator, fpsden=ifo_info.fps.denominator
        )

        return self._clip

    def index_files(self, _files: SPath | List[SPath]) -> None:
        files = [_files] if isinstance(_files, SPath) else _files

        if not len(files):
            raise FileNotFoundError('IsoFile: You should pass at least one file!')

        if self._idx_path is None:
            self._idx_path = self.indexer.get_idx_file_path(files[0])

        if not self._idx_path.is_file():
            self.indexer.index(files, self._idx_path)
        else:
            if self._idx_path.stat().st_size == 0:
                self._idx_path.unlink()
                self.indexer.index(files, self._idx_path)
            self.indexer.update_video_filenames(self._idx_path, files)

        idx_info = self.indexer.get_info(self._idx_path, 0)

        self.index_info[0] = idx_info

        if isinstance((idx_dgi := cast(Any, idx_info)), DGIndexFileInfo) and idx_dgi.footer.film == 100:
            self.indexer.indexer_kwargs |= {'fieldop': 2}

    def get_idx_info(
        self, index_path: SPath | None = None, index: int = 0
    ) -> D2VIndexFileInfo | DGIndexFileInfo:
        idx_path = index_path or self._idx_path or self.indexer.get_idx_file_path(self.iso_path)

        self.index_info[index] = self.indexer.get_info(idx_path, index)

        return cast(IndexFileType, self.index_info[index])

    def _split_chapters_clips(
        self, split_chapters: List[List[int]], dvd_menu_length: int
    ) -> Tuple[List[List[int]], List[vs.VideoNode]]:
        self._clip = cast(vs.VideoNode, self._clip)
        self._idx_path = cast(SPath, self._idx_path)

        durations = list(accumulate([0] + [frame[-1] for frame in split_chapters]))

        # Remove splash screen and DVD Menu
        clip = self._clip[dvd_menu_length:]

        # Trim per title
        clips = [clip[s:e] for s, e in zip(durations[:-1], durations[1:])]

        if dvd_menu_length:
            clips.append(self._clip[:dvd_menu_length])
            split_chapters.append([0, dvd_menu_length])

        return split_chapters, clips

    @lru_cache
    def get_ifo_info(self, mount_path: SPath) -> IFOFileInfo:
        ifo_files = [
            f for f in sorted(mount_path.glob('*.[iI][fF][oO]')) if f.stem != 'VIDEO_TS'
        ]

        program_chains = []

        m_ifos = len(ifo_files) > 1

        for ifo_file in ifo_files:
            with open(ifo_file, 'rb') as file:
                curr_pgci = vts_ifo.load_vts_pgci(cast(BufferedReader, file))
                program_chains += curr_pgci.program_chains[int(m_ifos):]

        split_chapters: List[List[int]] = []

        fps = Fraction(30000, 1001)

        for prog in program_chains:
            dvd_fps_s = [pb_time.fps for pb_time in prog.playback_times]
            if all(dvd_fps_s[0] == dvd_fps for dvd_fps in dvd_fps_s):
                fps = vts_ifo.FRAMERATE[dvd_fps_s[0]]
            else:
                raise ValueError('IsoFile: No VFR allowed!')

            raw_fps = 30 if fps.numerator == 30000 else 25

            split_chapters.append([0] + [
                pb_time.frames + (pb_time.hours * 3600 + pb_time.minutes * 60 + pb_time.seconds) * raw_fps
                for pb_time in prog.playback_times
            ])

        chapters = [
            list(accumulate(chapter_frames)) for chapter_frames in split_chapters
        ]

        return IFOFileInfo(chapters, fps, m_ifos)

    def split_titles(self) -> Tuple[List[vs.VideoNode], List[List[int]], vs.VideoNode, List[int]]:
        if self._idx_path is None:
            self._idx_path = self.indexer.get_idx_file_path(self.iso_path)

        if self._mount_path is None:
            self._mount_path = self._get_mount_path()

        if self._clip is None:
            self._clip = self.source()

        ifo_info = self.get_ifo_info(self._mount_path)

        idx_info = self.index_info[0] or self.indexer.get_info(self._idx_path, 0)
        self.index_info[0] = idx_info

        vts_0_size = idx_info.videos[0].size

        dvd_menu_length = cast(D2VIndexFileInfo, idx_info).header.ffflength if isinstance(
            self.indexer, D2VWitch) else (len(idx_info.frame_data) if vts_0_size > 2 << 12 else 0)

        self.split_chapters, self.split_clips = self._split_chapters_clips(ifo_info.chapters, dvd_menu_length)

        def _gen_joined_clip() -> vs.VideoNode:
            split_clips = cast(List[vs.VideoNode], self.split_clips)
            joined_clip = split_clips[0]

            if len(split_clips) > 1:
                for cclip in split_clips[1:]:
                    joined_clip += cclip

            return joined_clip

        def _gen_joined_chapts() -> List[int]:
            spl_chapts = cast(List[List[int]], self.split_chapters)
            joined_chapters = spl_chapts[0]

            if len(spl_chapts) > 1:
                for rrange in spl_chapts[1:]:
                    joined_chapters += [
                        r + joined_chapters[-1] for r in rrange if r != 0
                    ]

            return joined_chapters

        self.joined_clip = _gen_joined_clip()
        self.joined_chapters = _gen_joined_chapts()

        if self.joined_chapters[-1] > self._clip.num_frames:
            if not self.safe_indices:
                print(Warning(
                    "\n\tIsoFile: The chapters are broken, last few chapters "
                    "and negative indices will probably give out an error. "
                    "You can set safe_indices = True and trim down the chapters.\n"
                ))
            else:
                offset = 0
                split_chapters: List[List[int]] = [[] for _ in range(len(self.split_chapters))]

                for i in range(len(self.split_chapters)):
                    for j in range(len(self.split_chapters[i])):
                        if self.split_chapters[i][j] + offset < self._clip.num_frames:
                            split_chapters[i].append(self.split_chapters[i][j])
                        else:
                            split_chapters[i].append(
                                self._clip.num_frames - dvd_menu_length - len(self.split_chapters) + i + 2
                            )

                            for k in range(i + 1, len(self.split_chapters) - (int(dvd_menu_length > 0))):
                                split_chapters[k] = [0, 1]

                            if dvd_menu_length:
                                split_chapters[-1] = self.split_chapters[-1]

                            break
                    else:
                        offset += self.split_chapters[i][-1]
                        continue
                    break

                self.split_chapters, self.split_clips = self._split_chapters_clips(
                    split_chapters if dvd_menu_length == 0 else split_chapters[:-1],
                    dvd_menu_length
                )

                self.joined_clip = _gen_joined_clip()
                self.joined_chapters = _gen_joined_chapts()

        return self.split_clips, self.split_chapters, self.joined_clip, self.joined_chapters

    def get_title(
        self, clip_index: int | None = None, chapters: Range | List[Range] | None = None
    ) -> vs.VideoNode | List[vs.VideoNode]:
        if not self._clip:
            self._clip = self.source()

        if not self.split_clips:
            self.split_titles()

        if clip_index is not None:
            ranges = cast(List[List[int]], self.split_chapters)[clip_index]
            clip = cast(List[vs.VideoNode], self.split_clips)[clip_index]
        else:
            ranges = cast(List[int], self.joined_chapters)
            clip = cast(vs.VideoNode, self.joined_clip)

        rlength = len(ranges)

        start: int | None
        end: int | None

        if isinstance(chapters, int):
            start, end = ranges[0], ranges[-1]

            if chapters == rlength - 1:
                start = ranges[-2]
            elif chapters == 0:
                end = ranges[1]
            elif chapters < 0:
                start = ranges[rlength - 1 + chapters]
                end = ranges[rlength + chapters]
            else:
                start = ranges[chapters]
                end = ranges[chapters + 1]

            return clip[start:end]
        elif isinstance(chapters, tuple):
            start, end = chapters

            if start is None:
                start = 0
            elif start < 0:
                start = rlength - 1 + start

            if end is None:
                end = rlength - 1
            elif end < 0:
                end = rlength - 1 + end
            else:
                end += 1

            return clip[ranges[start]:ranges[end]]
        elif isinstance(chapters, list):
            return [cast(vs.VideoNode, self.get_title(clip_index, rchap)) for rchap in chapters]

        return clip

    def _mount_folder_path(self) -> SPath:
        if self.force_root:
            return self.iso_path

        if self.iso_path.name.upper() == self._subfolder:
            self.iso_path = self.iso_path.parent

        return self.iso_path / self._subfolder

    @abstractmethod
    def _get_mount_path(self) -> SPath:
        raise NotImplementedError()