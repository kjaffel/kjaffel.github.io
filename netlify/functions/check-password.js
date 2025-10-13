exports.handler = async (event) => {
    const { password } = JSON.parse(event.body);
    const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD; // from Netlify env

    return {
        statusCode: 200,
        body: JSON.stringify({ success: password === ADMIN_PASSWORD })
    };
};
