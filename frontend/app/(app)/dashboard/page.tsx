import { Card } from '@/components/ui/card';

export default function DashboardPage() {
  return (
    <section className="space-y-6">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">ダッシュボード</h1>
        <p className="mt-1 text-sm text-text-secondary">本日の概要 — D3 で実装予定</p>
      </header>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card className="p-5">
          <p className="text-xs text-text-muted">本日の訪問</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs text-text-muted">未割当</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs text-text-muted">アラート</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
      </div>
      {/* TODO: fetch from BACKEND_API_BASE_URL /api/v1/dashboard */}
    </section>
  );
}
