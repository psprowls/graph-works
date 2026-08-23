class Greeter
{
    public Greeter(string prefix)
    {
        Prefix = prefix;
    }

    public string Greet(string name)
    {
        return Prefix + name;
    }

    private string Prefix;
}
