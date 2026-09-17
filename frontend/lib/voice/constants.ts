/**
 * 音声記録の端末側の上限値（設計 §10-3 / レビュー M-3）。
 *
 * サーバ側の上限と同じ値をここに 1 つだけ置き、**投入前に弾く**ために使う。
 * 20 MiB を超える音声をキューへ積むと、端末の容量を食ったうえで送信時に 413 で
 * 破棄される（＝現場は「保存できた」と思ったまま失われる）。
 */

/**
 * 1 件あたりの音声の上限（20 MiB）。BE の `VISIT_AUDIO_MAX_BYTES` と揃える。
 *
 * 20 MiB は Vertex の inline データ上限。これを超える音声はサーバが受け取れず、
 * 分割の仕組みも無いので**端末側で先に止める**のが唯一の正しい扱いになる。
 */
export const VISIT_AUDIO_MAX_BYTES = 20 * 1024 * 1024;

/**
 * 自動停止に残す余裕（512 KiB）。
 *
 * `dataavailable` は 10 秒ぶんまとめて来るので、しきい値ちょうどで止めると
 * 最後のチャンクが上限を跨いでしまう。1 チャンク分の余裕を残して止める。
 */
export const AUDIO_SIZE_STOP_MARGIN_BYTES = 512 * 1024;

/** 累積バイト数がここに達したら自動停止する。 */
export const AUDIO_SIZE_STOP_BYTES = VISIT_AUDIO_MAX_BYTES - AUDIO_SIZE_STOP_MARGIN_BYTES;

/** 上限の 90% で警告する（あと少しで止まることを先に知らせる）。 */
export const AUDIO_SIZE_WARN_BYTES = Math.floor(VISIT_AUDIO_MAX_BYTES * 0.9);

/**
 * iPhone (AAC 32kbps 相当) のおおよそのレート = 1 分あたり約 1 MB。
 * 「あと何分録れるか」を現場の言葉にするためだけの目安。
 */
export const AUDIO_BYTES_PER_MINUTE = 1024 * 1024;

/** バイト数 → おおよその録音可能分数（最低 1 分）。 */
export function approxMinutes(bytes: number): number {
  return Math.max(1, Math.floor(bytes / AUDIO_BYTES_PER_MINUTE));
}

/** 孤児チャンクの掃除しきい値（24 時間）。 */
export const ORPHAN_CHUNK_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** バイト数を「12.3 MB」の形にする（案内文用）。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB';
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
