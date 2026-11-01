/**
 * Google Picker で保存先フォルダを選ぶ (休み・勤務表の設計 §7-3)。
 *
 * 読み込み方は Google の公式の例どおり:
 *   <script src="https://apis.google.com/js/api.js"> → gapi.load('client:picker', …)
 *   https://developers.google.com/workspace/drive/picker/guides/web-picker-sample
 * 権限は drive.file だけなので、フォルダの一覧はらく助からは読めない。Picker で選ばれた
 * フォルダだけにらく助が触れられる。
 *
 * 出す場所は 2 つ (タブ):「共有アイテム」(人から共有されたフォルダ。共有ドライブの中の
 * フォルダだけを共有されている場合もここに出る)・「共有ドライブ」(メンバーになっている
 * 共有ドライブ)。setEnableDrives の付いた view は共有ドライブしか出さないため view を分ける。
 * Picker は同じ ViewId の view を 1 つのタブにまとめてしまう (FOLDERS を 3 つ足したら最後の
 * 1 つしか出なかった、2026-11-01 本番で確認) ので、共有アイテムは DOCS、共有ドライブは
 * FOLDERS と ViewId を分ける。
 */

const GAPI_SRC = 'https://apis.google.com/js/api.js';
const FOLDER_MIME = 'application/vnd.google-apps.folder';

/* 使う分だけの最小の型 (Google の型の包みは入れない)。 */
interface PickerDocument {
  id: string;
  name?: string;
}
interface PickerResponse {
  action: string;
  docs?: PickerDocument[];
}
interface PickerDocsView {
  setSelectFolderEnabled(v: boolean): PickerDocsView;
  setIncludeFolders(v: boolean): PickerDocsView;
  setEnableDrives(v: boolean): PickerDocsView;
  setOwnedByMe(v: boolean): PickerDocsView;
  setLabel(v: string): PickerDocsView;
  setMimeTypes(v: string): PickerDocsView;
}
interface PickerBuilder {
  addView(v: PickerDocsView): PickerBuilder;
  setOAuthToken(t: string): PickerBuilder;
  setDeveloperKey(k: string): PickerBuilder;
  setAppId(id: string): PickerBuilder;
  setTitle(t: string): PickerBuilder;
  setLocale(l: string): PickerBuilder;
  enableFeature(f: string): PickerBuilder;
  setCallback(cb: (data: PickerResponse) => void): PickerBuilder;
  build(): { setVisible(v: boolean): void };
}
interface PickerNamespace {
  PickerBuilder: new () => PickerBuilder;
  DocsView: new (viewId?: string) => PickerDocsView;
  ViewId: { FOLDERS: string; DOCS: string };
  Feature: { SUPPORT_DRIVES: string };
  Action: { PICKED: string; CANCEL: string };
}
interface GoogleWindow {
  gapi?: { load(libs: string, cb: () => void): void };
  google?: { picker?: PickerNamespace };
}

let loading: Promise<PickerNamespace> | null = null;

/** api.js を 1 回だけ読み込み、picker を load する。 */
export function loadGooglePicker(): Promise<PickerNamespace> {
  const w = window as unknown as GoogleWindow;
  if (w.google?.picker) return Promise.resolve(w.google.picker);
  if (loading) return loading;
  loading = new Promise<PickerNamespace>((resolve, reject) => {
    const onGapi = () => {
      const gapi = (window as unknown as GoogleWindow).gapi;
      if (!gapi) {
        reject(new Error('Google の部品を読み込めませんでした'));
        return;
      }
      gapi.load('client:picker', () => {
        const picker = (window as unknown as GoogleWindow).google?.picker;
        if (picker) resolve(picker);
        else reject(new Error('Google の部品を読み込めませんでした'));
      });
    };
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${GAPI_SRC}"]`);
    if (existing && w.gapi) {
      onGapi();
      return;
    }
    const script = existing ?? document.createElement('script');
    script.addEventListener('load', onGapi, { once: true });
    script.addEventListener(
      'error',
      () => reject(new Error('Google の部品を読み込めませんでした')),
      { once: true },
    );
    if (!existing) {
      script.src = GAPI_SRC;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
  }).catch((e: unknown) => {
    loading = null;
    throw e;
  });
  return loading;
}

export interface PickedFolder {
  id: string;
  name: string | null;
}

/** フォルダを 1 つ選んでもらう。閉じたら null。 */
export async function pickDriveFolder(opts: {
  accessToken: string;
  apiKey: string;
  appId: string;
}): Promise<PickedFolder | null> {
  const picker = await loadGooglePicker();
  return new Promise<PickedFolder | null>((resolve) => {
    const folderView = (viewId: string) =>
      new picker.DocsView(viewId)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true)
        .setMimeTypes(FOLDER_MIME);
    new picker.PickerBuilder()
      .addView(folderView(picker.ViewId.DOCS).setOwnedByMe(false).setLabel('共有アイテム'))
      .addView(folderView(picker.ViewId.FOLDERS).setEnableDrives(true).setLabel('共有ドライブ'))
      .enableFeature(picker.Feature.SUPPORT_DRIVES)
      .setOAuthToken(opts.accessToken)
      .setDeveloperKey(opts.apiKey)
      .setAppId(opts.appId)
      .setLocale('ja')
      .setTitle('勤務表を置くフォルダを選んでください')
      .setCallback((data) => {
        if (data.action === picker.Action.PICKED) {
          const doc = data.docs?.[0];
          resolve(doc ? { id: doc.id, name: doc.name ?? null } : null);
        } else if (data.action === picker.Action.CANCEL) {
          resolve(null);
        }
      })
      .build()
      .setVisible(true);
  });
}
