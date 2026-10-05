const registerForm = document.getElementById("registerForm");

registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const username = document.getElementById("username").value.trim();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    const confirmPassword =
        document.getElementById("confirmPassword").value;

    if (password !== confirmPassword) {
        alert("Passwords do not match.");
        return;
    }

    try {
        const response = await fetch("/register", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                username: username,
                email: email,
                password: password
            })
        });

        const data = await response.json();

        console.log("Registration Response:", data);

        if (response.ok) {
            alert("Registration successful. Let's set up your typing profile.");
            window.location.href = data.redirect || "/enroll";
            return;
        }

        alert(data.message);

    } catch (error) {
        console.error("Registration request failed:", error);
        alert("Registration request failed.");
    }
});