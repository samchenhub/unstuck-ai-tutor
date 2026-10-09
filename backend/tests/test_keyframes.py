from unstuck.ingest.keyframes import dedupe_by_phash, extract_keyframes, plan_sample_times
from unstuck.models import NewKeyframe, Scene


def test_short_first_scene_merges_and_long_scene_splits():
    scenes = [Scene(0, 3), Scene(3, 20), Scene(20, 100)]
    # [0,3] is shorter than 5 s → merged into [0,20]; [20,100] is 80 s > 45 s → two 40 s pieces.
    assert plan_sample_times(scenes, duration=100) == [1.0, 21.0, 61.0]


def test_short_middle_scene_merges_into_previous():
    scenes = [Scene(0, 10), Scene(10, 12), Scene(12, 30)]
    assert plan_sample_times(scenes, duration=30) == [1.0, 13.0]


def test_cap_of_one_frame_per_ten_seconds():
    scenes = [Scene(i * 6.0, (i + 1) * 6.0) for i in range(5)]  # five 6 s scenes, 30 s total
    # cap = 30 // 10 = 3 frames, picked evenly from [1, 7, 13, 19, 25]
    assert plan_sample_times(scenes, duration=30) == [1.0, 7.0, 19.0]


def test_no_scenes_means_whole_video_split_by_max_len():
    assert plan_sample_times([], duration=90) == [1.0, 46.0]


def test_hard_cap_of_300_frames():
    scenes = [Scene(i * 6.0, (i + 1) * 6.0) for i in range(1000)]
    assert len(plan_sample_times(scenes, duration=6000)) == 300


def test_tiny_scene_samples_its_midpoint():
    assert plan_sample_times([Scene(0, 1.0)], duration=1.0) == [0.5]


def kf(t, phash):
    return NewKeyframe(t=t, image_path=f"/f/{t}.jpg", phash=phash)


def test_dedupe_by_phash_compares_with_last_kept():
    frames = [
        kf(1, "0000000000000000"),
        kf(2, "0000000000000001"),  # distance 1 → duplicate of frame 1
        kf(3, "ffffffffffffffff"),  # distance 64 → kept
        kf(4, "fffffffffffffff0"),  # distance 4 from frame 3 → duplicate
    ]
    assert [f.t for f in dedupe_by_phash(frames, threshold=5)] == [1, 3]


def test_extract_keyframes_on_real_video(sample_video, tmp_path):
    frames = extract_keyframes(sample_video, duration=3.0, out_dir=tmp_path / "frames")
    assert len(frames) == 1  # 3 s video → cap of one frame
    assert (tmp_path / "frames").exists()
    assert len(frames[0].phash) == 16
