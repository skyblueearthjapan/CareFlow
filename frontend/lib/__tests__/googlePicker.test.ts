import { afterEach, describe, expect, it } from 'vitest';

import { pickDriveFolder } from '../googlePicker';

/** google.picker の偽物。作られた view と builder への呼び出しを記録する。 */
function installFakePicker() {
  const views: Record<string, unknown>[] = [];
  const builderCalls: Record<string, unknown[]> = { addView: [], enableFeature: [] };
  let callback: ((d: { action: string; docs?: { id: string; name?: string }[] }) => void) | null =
    null;

  class DocsView {
    state: Record<string, unknown> = {};
    constructor(viewId?: string) {
      this.state.viewId = viewId;
      views.push(this.state);
    }
    setSelectFolderEnabled(v: boolean) {
      this.state.selectFolder = v;
      return this;
    }
    setIncludeFolders(v: boolean) {
      this.state.includeFolders = v;
      return this;
    }
    setEnableDrives(v: boolean) {
      this.state.enableDrives = v;
      return this;
    }
    setOwnedByMe(v: boolean) {
      this.state.ownedByMe = v;
      return this;
    }
    setLabel(v: string) {
      this.state.label = v;
      return this;
    }
    setMimeTypes(v: string) {
      this.state.mime = v;
      return this;
    }
  }
  class PickerBuilder {
    addView(v: DocsView) {
      builderCalls.addView.push(v.state);
      return this;
    }
    enableFeature(f: string) {
      builderCalls.enableFeature.push(f);
      return this;
    }
    setOAuthToken() {
      return this;
    }
    setDeveloperKey() {
      return this;
    }
    setAppId() {
      return this;
    }
    setTitle() {
      return this;
    }
    setLocale() {
      return this;
    }
    setCallback(cb: typeof callback) {
      callback = cb;
      return this;
    }
    build() {
      return { setVisible: () => undefined };
    }
  }
  (window as unknown as { google: unknown }).google = {
    picker: {
      PickerBuilder,
      DocsView,
      ViewId: { FOLDERS: 'folders' },
      Feature: { SUPPORT_DRIVES: 'sdr' },
      Action: { PICKED: 'picked', CANCEL: 'cancel' },
    },
  };
  return {
    views,
    builderCalls,
    fire: (d: Parameters<NonNullable<typeof callback>>[0]) => callback?.(d),
  };
}

afterEach(() => {
  delete (window as unknown as { google?: unknown }).google;
});

describe('pickDriveFolder', () => {
  it('共有アイテム・マイドライブ・共有ドライブの 3 つのタブでフォルダだけを出す', async () => {
    const fake = installFakePicker();
    const p = pickDriveFolder({ accessToken: 't', apiKey: 'k', appId: 'a' });
    await Promise.resolve();
    await Promise.resolve();

    const added = fake.builderCalls.addView as Record<string, unknown>[];
    expect(added.map((v) => v.label)).toEqual(['共有アイテム', 'マイドライブ', '共有ドライブ']);
    expect(added[0].ownedByMe).toBe(false);
    expect(added[0].enableDrives).toBeUndefined();
    expect(added[1].ownedByMe).toBe(true);
    expect(added[2].enableDrives).toBe(true);
    for (const v of added) {
      expect(v.selectFolder).toBe(true);
      expect(v.mime).toBe('application/vnd.google-apps.folder');
    }
    expect(fake.builderCalls.enableFeature).toEqual(['sdr']);

    fake.fire({ action: 'picked', docs: [{ id: 'F1', name: '勤務表' }] });
    await expect(p).resolves.toEqual({ id: 'F1', name: '勤務表' });
  });

  it('キャンセルしたら null', async () => {
    const fake = installFakePicker();
    const p = pickDriveFolder({ accessToken: 't', apiKey: 'k', appId: 'a' });
    await Promise.resolve();
    await Promise.resolve();
    fake.fire({ action: 'cancel' });
    await expect(p).resolves.toBeNull();
  });
});
