import asyncpg


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    employee_id VARCHAR(10) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100),
    country VARCHAR(50),
    role VARCHAR(100),
    department VARCHAR(100),
    manager_id VARCHAR(10),
    salary DECIMAL(10,2),
    currency VARCHAR(3) DEFAULT 'USD',
    vacation_days_total INT DEFAULT 20,
    vacation_days_used INT DEFAULT 0,
    start_date DATE
);
"""

SEED_SQL = """
INSERT INTO employees
    (employee_id, name, email, country, role, department, manager_id,
     salary, currency, vacation_days_total, vacation_days_used, start_date)
VALUES
    ('E001', 'Alice Johnson',  'alice@demo.com',   'US',      'Software Engineer',    'Engineering', 'E005', 95000,  'USD', 20, 8,  '2021-03-15'),
    ('E002', 'Ben Schmidt',    'ben@demo.com',     'Germany', 'Product Manager',      'Product',     'E005', 72000,  'EUR', 30, 12, '2019-07-01'),
    ('E003', 'Clara Patel',    'clara@demo.com',   'UK',      'HR Specialist',        'HR',          'E006', 48000,  'GBP', 28, 5,  '2022-11-20'),
    ('E004', 'David Lee',      'david@demo.com',   'US',      'Sales Representative', 'Sales',       'E006', 68000,  'USD', 20, 15, '2020-01-10'),
    ('E005', 'Eva Martinez',   'eva@demo.com',     'US',      'Engineering Manager',  'Engineering', 'E000',   130000, 'USD', 20, 3,  '2017-05-22'),
    ('E006', 'Frank Nguyen',   'frank@demo.com',   'US',      'Director of HR',       'HR',          'E000',   150000, 'USD', 25, 7,  '2016-09-01'),
    ('lmolkova', 'Liudmila Molkova', 'liudmila@demo.com', 'US', 'Software Engineer',  'Engineering', 'E005', 112000, 'USD', 30, 20, '2025-08-01'),
    ('E000', 'Alex Morgan',    'alex@demo.com',    'US',      'CEO',                  '',            NULL, 212000, 'USD', 30, 20, '2020-08-01')

ON CONFLICT (employee_id) DO NOTHING;
"""


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url)
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(SEED_SQL)
    return pool


async def get_employee(pool: asyncpg.Pool, employee_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM employees WHERE employee_id = $1", employee_id
        )
    if row is None:
        return None
    return dict(row)


async def get_manager(pool: asyncpg.Pool, manager_id: str) -> dict | None:
    return await get_employee(pool, manager_id)
