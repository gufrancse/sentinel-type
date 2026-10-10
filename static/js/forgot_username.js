document.getElementById("forgotUsernameForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const email = document.getElementById("email").value.trim();
    const statusMessage = document.getElementById("statusMessage");
    const submitButton = event.target.querySelector("button");

    submitButton.disabled = true;
    statusMessage.textContent = "Sending...";
    statusMessage.style.color = "#94a3b8";

    try {
        const response = await fetch("/forgot-username", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email })
        });

        const data = await response.json();

        statusMessage.textContent = data.message;
        statusMessage.style.color = data.success ? "lightgreen" : "salmon";
    } catch (error) {
        statusMessage.textContent = "Something went wrong. Please try again.";
        statusMessage.style.color = "salmon";
    } finally {
        submitButton.disabled = false;
    }
});