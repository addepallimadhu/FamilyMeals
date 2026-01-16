async function fetchUsers() {
  const res = await fetch('/api/users');
  return res.json();
}

async function listBookings(phone) {
  const url = phone ? `/api/bookings?phone=${encodeURIComponent(phone)}` : '/api/bookings';
  const res = await fetch(url);
  return res.json();
}

async function createBooking(body) {
  const res = await fetch('/api/bookings', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body)
  });
  return res.json().then(d => ({ ok: res.ok, status: res.status, body: d }));
}

document.addEventListener('DOMContentLoaded', async () => {
  const users = await fetchUsers();
  const ul = document.getElementById('users');
  ul.innerHTML = users.map(u => `<li>${u.name} — ${u.phone} — no_shows: ${u.no_show_count} — restricted: ${u.restricted}</li>`).join('');

  document.getElementById('create').addEventListener('click', async () => {
    const organizer = document.getElementById('organizer').value.trim();
    const participants = document.getElementById('participants').value.split(',').map(s=>s.trim()).filter(Boolean);
    const start = document.getElementById('start').value.trim();
    const end = document.getElementById('end').value.trim();
    const note = document.getElementById('note').value.trim();
    const payload = { organizer_phone: organizer, participants_phones: participants, start_ts: start, end_ts: end, note };
    const r = await createBooking(payload);
    const out = document.getElementById('createResult');
    if (r.ok) {
      out.innerText = `Created: ${JSON.stringify(r.body, null, 2)}`;
    } else {
      out.innerText = `Error (${r.status}): ${JSON.stringify(r.body, null, 2)}`;
    }
  });

  document.getElementById('list').addEventListener('click', async () => {
    const phone = document.getElementById('listPhone').value.trim();
    const data = await listBookings(phone || null);
    document.getElementById('bookingsOut').innerText = JSON.stringify(data, null, 2);
  });
});