async function loadReports() {
  try {
    const response = await fetch('/api/reports');
    if (!response.ok) {
      throw new Error('Could not load report data');
    }

    const data = await response.json();
    renderCategoryChart(data.categoryTotals || []);
    renderExpenseChart(data.monthlyExpenses || []);
    renderSavingsChart(data.monthlySavings || []);
  } catch (error) {
    console.error(error);
  }
}

const baseChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      position: 'bottom'
    }
  }
};

function renderCategoryChart(rows) {
  const ctx = document.getElementById('categoryChart');
  if (!ctx) return;

  const labels = rows.map((row) => row.category);
  const values = rows.map((row) => Number(row.total));

  new Chart(ctx, {
    type: 'pie',
    data: {
      labels: labels.length ? labels : ['No data'],
      datasets: [{
        data: values.length ? values : [1],
        backgroundColor: [
          '#0f766e', '#14b8a6', '#0891b2', '#4f46e5', '#9333ea',
          '#f59e0b', '#f97316', '#ef4444', '#84cc16', '#64748b'
        ]
      }]
    },
    options: baseChartOptions
  });
}

function renderExpenseChart(rows) {
  const ctx = document.getElementById('expenseChart');
  if (!ctx) return;

  const labels = rows.map((row) => row.month);
  const values = rows.map((row) => Number(row.total));

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels.length ? labels : ['No data'],
      datasets: [{
        label: 'Expenses',
        data: values.length ? values : [0],
        backgroundColor: '#0891b2',
        maxBarThickness: 48
      }]
    },
    options: {
      ...baseChartOptions,
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            maxTicksLimit: 6
          }
        }
      }
    }
  });
}

function renderSavingsChart(rows) {
  const ctx = document.getElementById('savingsChart');
  if (!ctx) return;

  const labels = rows.map((row) => row.month);
  const values = rows.map((row) => Number(row.savings));

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels.length ? labels : ['No data'],
      datasets: [{
        label: 'Savings',
        data: values.length ? values : [0],
        borderColor: '#0f766e',
        backgroundColor: 'rgba(15, 118, 110, 0.12)',
        fill: true,
        tension: 0.25,
        pointRadius: 4
      }]
    },
    options: {
      ...baseChartOptions,
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            maxTicksLimit: 6
          }
        }
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', loadReports);
