async function apiJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    return { response, data };
}

function apiPostJson(url, payload = {}) {
    return apiJson(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
}

window.apiJson = apiJson;
window.apiPostJson = apiPostJson;
