const registerForm = document.getElementById("registerForm");
const registerStatus = document.getElementById("registerStatus");
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
            registerStatus.textContent =
                `Registration successful! A verification link has been sent to ${email}. ` +
                `Redirecting you to set up your typing profile - don't forget to verify ` +
                `your email before your next login.`;
            registerStatus.style.color = "lightgreen";

            setTimeout(() => {
                window.location.href = data.redirect || "/enroll";
            }, 2500);

            return;
        }

        registerStatus.textContent = data.message;
        registerStatus.style.color = "salmon";

        alert(data.message);

    } catch (error) {
        console.error("Registration request failed:", error);
        alert("Registration request failed.");
    }
});