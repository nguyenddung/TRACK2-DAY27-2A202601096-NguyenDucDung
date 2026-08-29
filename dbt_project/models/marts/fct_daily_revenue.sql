-- If the customer dimension ever has more than one active row for the same
-- customer_id (a broken SCD close-out), joining against it directly fans out
-- matching orders and silently inflates revenue -- no SQL error, just a wrong
-- number. `duplicate_active_customer_row_does_not_inflate_revenue` in
-- unit_tests.yml pins this down: it fails against a naive join and passes
-- once active_customers is deduplicated to at most one row per customer_id.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
active_customers as (
    select *
    from {{ ref('stg_customers') }}
    where is_active = true
    qualify row_number() over (partition by customer_id order by valid_from desc) = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
