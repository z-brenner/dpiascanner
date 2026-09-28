// In-memory stand-in for the generated Prisma client, used by tests and the exercise
// script so the app can run without Postgres. Implements only what the app calls.
type Row = Record<string, unknown> & { id: number };

function table() {
  const rows: Row[] = [];
  return {
    async create({ data }: { data: Record<string, unknown> }): Promise<Row> {
      const row = { ...data, id: rows.length + 1, createdAt: new Date() } as Row;
      rows.push(row);
      return row;
    },
    async findUnique({ where }: { where: Record<string, unknown> }): Promise<Row | null> {
      const [key, value] = Object.entries(where)[0] ?? [];
      return rows.find((r) => key !== undefined && r[key] === value) ?? null;
    },
    async update({ where, data }: { where: { id: number }; data: Record<string, unknown> }) {
      const row = rows.find((r) => r.id === where.id);
      if (row) Object.assign(row, data);
      return row;
    },
    async deleteMany(): Promise<{ count: number }> {
      return { count: 0 };
    },
  };
}

export function createMemoryClient() {
  return {
    user: table(),
    healthRecord: table(),
    signupMetric: table(),
    referralContact: table(),
  };
}
