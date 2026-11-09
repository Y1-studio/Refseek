SHELL = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Refseek accounts</title>
<style>html{color-scheme:dark}body{background:#10151c;color:#e6edf3;font:1rem system-ui;
margin:0}main{max-width:50rem;margin:2rem auto;padding:1.25rem}form{display:grid;gap:.8rem}
label{display:grid;gap:.3rem}input,button{font:inherit;padding:.55rem;border-radius:.4rem}
button{cursor:pointer}fieldset{border:1px solid #66758c;max-height:20rem;overflow:auto}
fieldset label{display:flex;gap:.5rem;overflow-wrap:anywhere}a{color:#8fc5ff}#message{min-height:1.5rem}
#accounts article{margin:.75rem 0;border-bottom:1px solid #374556;padding:.5rem;overflow-wrap:anywhere}
#accounts button{margin:.4rem .4rem 0 0}small{color:#adbac8}</style></head><body><main>'''


LOGIN = SHELL + '''<h1>Sign in to Refseek</h1><form id="login">
<label>Username<input name="username" required autocomplete="username" maxlength="64"></label>
<label>Password<input name="password" type="password" required autocomplete="current-password" maxlength="128"></label>
<button>Sign in</button></form><p id="message" role="status"></p>
<script>
const form = document.getElementById('login'), message = document.getElementById('message');
form.onsubmit = async event => {
  event.preventDefault();
  const button = form.querySelector('button'); button.disabled = true;
  try {
    const response = await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    if (response.ok) { location.replace('/'); return; }
    message.textContent = (await response.json()).error;
  } catch (_) { message.textContent = 'Could not sign in. Try again.'; }
  finally { button.disabled = false; }
};
</script></main></body></html>'''


USERS = SHELL + '''<a href="/">Back to search</a><h1>Users and folder access</h1>
<p>Guest accounts can search and open files from the folders you select, including subfolders. No selected folders means no library access.</p>
<form id="user">
  <p id="editing">New guest account</p>
  <label>Username<input name="username" maxlength="64" required autocomplete="off"></label>
  <label>Password<input name="password" type="password" maxlength="128" autocomplete="new-password"></label>
  <small>Use at least 15 characters for a new account. Leave the password blank to update access only.</small>
  <fieldset id="folders"><legend>Allowed folders</legend></fieldset>
  <button type="submit">Create user</button><button type="button" id="new_user">New user</button>
</form>
<p id="message" role="status"></p><h2>Guest accounts</h2><div id="accounts"></div>
<script>
const form = document.getElementById('user'), message = document.getElementById('message');
const folders = document.getElementById('folders'), accounts = document.getElementById('accounts');
let editing = null;
function resetEditor() {
  editing = null; form.reset(); form.elements.username.readOnly = false;
  document.getElementById('editing').textContent = 'New guest account';
  form.querySelector('button[type="submit"]').textContent = 'Create user';
}
document.getElementById('new_user').onclick = resetEditor;
const csrf = document.querySelector('meta[name="refseek-csrf"]').content;
async function request(path, payload) {
  const options = payload ? {method:'POST',headers:{'Content-Type':'application/json','X-Refseek-CSRF':csrf},
    body:JSON.stringify(payload)} : {};
  const response = await fetch(path, options), data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}
async function load() {
  const data = await request('/api/users');
  folders.querySelectorAll('label,small').forEach(node => node.remove());
  for (const path of data.folders) {
    const label = document.createElement('label'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.name = 'folder'; checkbox.value = path;
    label.append(checkbox, path); folders.append(label);
  }
  if (!data.folders.length) {
    const note = document.createElement('small'); note.textContent = 'No indexed folders. A guest saved without folders has no library access.'; folders.append(note);
  }
  accounts.replaceChildren();
  for (const user of data.users) {
    const article = document.createElement('article'), title = document.createElement('strong');
    title.textContent = user.username;
    const list = document.createElement('p'); list.textContent = user.folders.length ? user.folders.join(' · ') : 'No folder access';
    const edit = document.createElement('button'); edit.textContent = 'Edit access';
    edit.onclick = () => {
      editing = user.username; form.elements.username.readOnly = true;
      document.getElementById('editing').textContent = 'Editing: ' + user.username;
      form.querySelector('button[type="submit"]').textContent = 'Save changes';
      form.elements.username.value = user.username; form.elements.password.value = '';
      for (const checkbox of folders.querySelectorAll('input')) checkbox.checked = user.folders.includes(checkbox.value);
      form.elements.username.focus();
    };
    const remove = document.createElement('button'); remove.textContent = 'Remove user';
    remove.onclick = async () => {
      if (!confirm('Remove access for ' + user.username + '?')) return;
      try { await request('/api/users/remove',{username:user.username}); if (editing === user.username) resetEditor(); await load(); message.textContent = 'Access removed.'; }
      catch (error) { message.textContent = error.message; }
    };
    article.append(title,list,edit,remove); accounts.append(article);
  }
  if (!data.users.length) accounts.textContent = 'No guest accounts yet.';
}
form.onsubmit = async event => {
  event.preventDefault();
  const values = new FormData(form), button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await request('/api/users',{username:values.get('username'),password:values.get('password'),folders:values.getAll('folder'),create:editing === null});
    resetEditor(); await load(); message.textContent = 'User saved. Access and password changes sign the user out.';
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
};
load().catch(error => { message.textContent = error.message; });
</script></main></body></html>'''
