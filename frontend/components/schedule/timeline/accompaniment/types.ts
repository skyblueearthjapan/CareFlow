/**
 * 同行モード — タイムライン盤 (週/日) と親 (CourseDayTablePanel) の間の契約。
 * 同行者は新人に限らない (general-accompaniment-design.md §4)。
 *
 * 盤は「表示専用」を保つため、同行モードのロジック (選択状態・重複判定・PUT) は
 * すべて親側の `useAccompanimentController` が持ち、盤にはこの binding を 1 つ渡すだけ
 * にする (§7.1「本体への侵襲を最小に」)。
 */

/** 週タイムラインの 1 訪問ぶん (重複判定・実効集合の解決に使う)。親が組む。 */
export interface AccompanimentWeekVisit {
  visitId: string;
  patientId: string;
  patientName: string | null;
  /** 0=月..6=日。同一日グルーピングのキー。 */
  weekday: number;
  /** その曜日のコースインスタンス id (findCourseForTemplate で解決)。未生成なら null。 */
  courseId: string | null;
  courseTemplateId: string;
  /** コース表示ラベル (例: '稲毛A')。重複メッセージ表示用。 */
  courseLabel: string | null;
  /** 開始 (0時起点の分)。null=時刻不明。 */
  startMin: number | null;
  /** 終了 (0時起点の分)。null=時刻不明。 */
  endMin: number | null;
  /**
   * 同住所グループキー (buildSameAddressKey(lat,lng))。同住所×同時刻ペアの
   * 90分占有ルールを重複判定から免除するために使う。null=座標なし。
   */
  sameAddressKey: string | null;
}

/**
 * 盤へ渡す同行 binding。`active=false` のときは常時表示 (§7.2) のバッジ解決のみ、
 * `active=true` のときは選択トグル + 重複ハイライトを担う。
 */
export interface AccompanimentBinding {
  /** モード中か。false=通常表示 (バッジのみ)。 */
  active: boolean;

  // --- モード中 (active) の選択状態 -----------------------------------------
  /** その曜日コース全体が選択済みか。 */
  isCourseSelected: (courseId: string | null) => boolean;
  /** その訪問が実効選択集合 (コース経由 ∪ 個別) に入っているか。 */
  isVisitSelected: (visitId: string) => boolean;
  /** その訪問が「選択済みコース」に内包されているか (=個別クリック不可)。 */
  isVisitInSelectedCourse: (visitId: string) => boolean;
  /** その訪問が時間重複に関与しているか (赤枠表示用)。 */
  isVisitOverlapping: (visitId: string) => boolean;
  /** その曜日コース全体の選択をトグル。meta は defaults 化 (毎週の既定) に必要。 */
  toggleCourse: (courseId: string | null, templateId: string, weekday: number) => void;
  /** 訪問個別の選択をトグル (選択済みコース内なら no-op)。 */
  toggleVisit: (visitId: string) => void;

  // --- 入力補助 (二択フィルタ・general-accompaniment-design.md 確定#2) --------
  /** コース(曜日)単位のクリックを受け付けるか。false=ヘッダは選択不可。 */
  isCourseArmed: boolean;
  /** 患者(訪問)単位のクリックを受け付けるか。false=カード/行は選択不可。 */
  isVisitArmed: boolean;

  // --- 常時表示 (inactive・§7.2) --------------------------------------------
  /**
   * 個別リンク先訪問に出す同行スタッフ名。1 訪問に複数名が付きうる
   * (確定#5) ため配列。空配列=なし。
   */
  visitBadgeName: (visitId: string) => string[];
  /** コース丸ごと同行の列ヘッダに出す同行スタッフ名 (複数可)。空配列=なし。 */
  courseBadgeName: (courseId: string | null) => string[];

  // --- 週盤用リゾルバ (週盤は templateId+weekday しか持たない) ----------------
  /** (course_template_id, weekday) → その週のコースインスタンス id。未生成なら null。 */
  resolveCourseId: (templateId: string, weekday: number) => string | null;
}
